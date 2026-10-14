"""Worker-owned protected SWAP commands. Receipts never authorize transport."""
import asyncio
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Protocol

from hummingbot.core.data_type.common import PositionAction, PositionMode, TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.protected_swap import SwapSendPermit
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    CommitUnknown,
    OwnerMode,
    OwnerToken,
    StateBinding,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import signed_decimal
from hummingbot.strategy_v2.life_liquidity.storage.joint import FileJointRepository
from hummingbot.strategy_v2.life_liquidity.storage.products import ProductCut


def _storage_json(value):
    if type(value) is float:
        if not math.isfinite(value):
            raise StorageError('SWAP_NONFINITE_VALUE')
        return str(signed_decimal(Decimal(str(value))))
    if isinstance(value, dict):
        return {k: _storage_json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_storage_json(v) for v in value]
    return thaw(value)


def _config_data(config):
    data = config.model_dump(mode='json')
    if data['timestamp'] is not None:
        data['timestamp'] = str(signed_decimal(Decimal(str(data['timestamp']))))
    return _storage_json(data)


@dataclass(frozen=True)
class PrepareSwap:
    operation_id: str
    client_order_id: str
    config_payload: bytes

    def __post_init__(self):
        identifier(self.operation_id)
        if (len(self.operation_id) > 96 or type(self.client_order_id) is not str
                or re.fullmatch(r'[A-Za-z0-9]{1,32}', self.client_order_id) is None
                or type(self.config_payload) is not bytes or len(self.config_payload) > 16384):
            raise StorageError('SWAP_COMMAND_INVALID')
        config = self.config()
        identifier(config.id)
        if encode(_config_data(config)) != self.config_payload:
            raise StorageError('SWAP_CONFIG_ENCODING_INVALID')

    def config(self):
        try:
            values = json.loads(self.config_payload)
            config = OrderExecutorConfig.model_validate(values)
            # Base validation materializes a wall-clock time for explicit null.
            # Keep the original snapshot; timestamps do not create permission.
            if values['timestamp'] is None:
                config = config.model_copy(update={'timestamp': None})
            return config
        except (ValueError, TypeError, KeyError):
            raise StorageError('SWAP_CONFIG_INVALID') from None

    def json(self):
        return {'operation_id': self.operation_id, 'client_order_id': self.client_order_id,
                'config': _config_data(self.config())}

    @classmethod
    def from_config(cls, operation_id, client_order_id, config):
        if type(config) is not OrderExecutorConfig:
            raise StorageError('SWAP_CONFIG_INVALID')
        return cls(operation_id, client_order_id, encode(_config_data(config)))

    @classmethod
    def from_json(cls, data):
        return cls(data['operation_id'], data['client_order_id'], encode(thaw(data['config'])))


@dataclass(frozen=True)
class SwapReceipt:
    binding: StateBinding
    operation_id: str
    state: str
    permit: SwapSendPermit
    replayed: bool
    boundary_checked_at_ms: int | None = None
    boundary_valid_until_ms: int | None = None
    reason_code: str = 'SWAP_CHECKPOINT_RECORDED'
    atomic_scope: AtomicScope = AtomicScope.RECOVERABLE_SAGA


class SwapRepository(Protocol):
    async def prepare(self, command: PrepareSwap, *, cut: ProductCut, owner: OwnerToken) -> SwapReceipt:
        ...

    async def arm(self, operation_id: str, *, cut: ProductCut, owner: OwnerToken) -> SwapReceipt:
        ...

    async def acknowledge(self, operation_id: str, *, exchange_order_id: str, owner: OwnerToken) -> SwapReceipt:
        ...


def _permit(data):
    values = thaw(data)
    for key in ('price_usdt', 'quantity_base', 'contracts', 'contract_value_life'):
        values[key] = Decimal(values[key])
    for key, cls in (('side', TradeType), ('position_action', PositionAction), ('position_mode', PositionMode)):
        values[key] = cls[values[key]]
    return SwapSendPermit(**values)


class FileSwapRepository:
    atomic_scope = AtomicScope.RECOVERABLE_SAGA

    def __init__(self, joint):
        if type(joint) is not FileJointRepository or joint._recovery.sender._storage_repository is not None:
            raise StorageError('SWAP_REPOSITORY_ASSEMBLY_INVALID')
        self.joint, self.store, self.binding = joint, joint.store, joint.binding
        self.sender = joint._recovery.sender
        self.application = None
        self._known_wires = ()

    def _policy(self):
        return {'joint': self.joint._policy(), 'sender': self.sender._policy()}

    def _initialize(self):
        meta = self._guard()
        previous = meta.resources.get('swap_repository_policy')
        if previous is None:
            if (not self.store._created_offline or self.sender.wal.all_records()
                    or meta.resources.get('swap_operations')):
                raise StorageError('SWAP_REPOSITORY_IMPORT_REQUIRED')
            self.store._commit(meta.cut, self.joint.owner, 'swap:bind',
                               (('swap_repository_policy', encode(self._policy())),))
        elif thaw(previous) != self._policy():
            raise StorageError('SWAP_REPOSITORY_POLICY_MISMATCH')

    def _guard(self, owner=None, *, admission=False):
        meta = self.joint._guard()
        if self.sender is not self.joint._recovery.sender or self.sender._storage_repository not in (None, self):
            raise StorageError('SWAP_REPOSITORY_BINDING_MISMATCH')
        if self.sender._storage_repository is self and thaw(meta.resources.get('swap_repository_policy')) != self._policy():
            raise StorageError('SWAP_REPOSITORY_POLICY_MISMATCH')
        if owner is not None and (owner != self.joint.owner or meta.ownership.token != owner):
            raise StorageError('SWAP_OWNER_MISMATCH')
        if admission:
            now = utc(self.store._clock())
            if (meta.ownership.mode is not OwnerMode.ACTIVE or not owner.valid_from <= now < owner.expires_at
                    or getattr(self.store, '_cold_unarmed', False) or not self.sender._runtime_enabled
                    or self.application is not None and not self.application.local_ready()):
                raise StorageError('SWAP_ADMISSION_REVOKED')
            if (any(v['state'] in ('PREPARING', 'ARMING', 'ACKING') for v in meta.resources.get('swap_operations', {}).values())
                    or any(v['state'] != 'APPLIED' for key in ('product_operations', 'recorder_outbox', 'recorder_progress',
                                                               'recorder_positions', 'recorder_events')
                           for v in meta.resources.get(key, {}).values())
                    or hasattr(self.store, '_incomplete') and self.store._incomplete(meta.resources)):
                raise StorageUnavailable('SWAP_REPAIR_REQUIRED')
        self.sender.wal.assert_verified()
        self.joint._recovery.capital.claims(include_settled=True)
        self.sender.journal._read()
        self._known_wires = tuple(r.client_order_id for r in self.sender.wal.all_records()
                                  if r.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND'))
        return meta

    def _cut(self):
        self._guard()
        paths = {'wal': self.sender.wal.path, 'actions': self.sender.journal.path,
                 'capital': self.joint._recovery.capital.journal.path, 'requests': self.sender.request_budget.path}
        from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import read_limited
        hashes = tuple(sorted((name, hashlib.sha256(read_limited(path, self.store.limits.max_checkpoint_bytes)).hexdigest())
                              for name, path in paths.items()))
        return ProductCut(self.store._read().cut, hashes)

    async def cut(self, binding):
        self.store._assert_binding(binding)
        return await self.store._call(self._cut)

    def _image(self):
        return _storage_json({'records': {r.intent_id: asdict(r) for r in self.sender.wal.all_records()},
                              'actions': self.sender.journal._read()})

    def _claims(self):
        from hummingbot.strategy_v2.life_liquidity.shared_capital import _json
        return {key: _json(asdict(v)) for key, v in self.joint._recovery.capital.claims(include_settled=True).items()}

    def _publish(self, operations, owner, key):
        meta = self.store._read()
        self.store._commit(meta.cut, owner, key, (('swap_operations', encode(operations)),), observation=True)

    def _receipt(self, row, replayed=False):
        if row['identity'] != fingerprint({'command': row['command'], 'cut': row['cut'], 'owner': row['owner']}):
            raise StorageError('SWAP_OPERATION_ORIGIN_UNVERIFIED')
        return SwapReceipt(self.binding, row['command']['operation_id'], row['state'], _permit(row['permit']),
                           replayed, row.get('boundary_checked_at_ms'), row.get('boundary_valid_until_ms'),
                           row.get('reason', 'SWAP_CHECKPOINT_RECORDED'))

    def _prepare(self, command, cut, owner):
        meta = self._guard(owner)
        operations = thaw(meta.resources.get('swap_operations', {}))
        identity = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        previous = operations.get(command.operation_id)
        if previous is not None:
            if previous['identity'] != identity:
                raise StorageConflict('SWAP_OPERATION_CONFLICT')
            if previous['state'] == 'PREPARING':
                raise StorageUnavailable('SWAP_PREPARE_REQUIRES_RECOVERY')
            return self._receipt(previous, True)
        self._guard(owner, admission=True)
        if self._cut() != cut:
            raise StorageConflict('SWAP_CUT_STALE')
        config = command.config()
        permit = self.sender._permit_for(config, command.client_order_id)
        if any(row['command']['config']['id'] == config.id or row['command']['client_order_id'] == command.client_order_id
               for row in operations.values()):
            raise StorageConflict('SWAP_PERMANENT_ID_REUSED')
        if (config.id in self._claims() or any(r.intent_id == config.id or r.client_order_id == command.client_order_id
                                               for r in self.sender.wal.all_records())):
            raise StorageError('SWAP_NATIVE_IMPORT_REQUIRED')
        before = {**self._image(), 'claims': self._claims()}
        row = {'command': command.json(), 'permit': self.sender._entry(config, permit)['permit'],
               'identity': identity, 'owner': owner.json(), 'cut': cut.json(), 'state': 'PREPARING',
               'before': before, 'boundary_consumed': False, 'exchange_order_id': None}
        operations[command.operation_id] = row
        self._publish(operations, owner, 'swap-prepare-start:' + command.operation_id)
        self._known_wires = tuple(sorted(set(self._known_wires) | {command.client_order_id}))
        try:
            self.sender._prepare_permit(config, permit)
            if self._verify_prepared(row)[3].state != 'PREPARED':
                raise StorageError('SWAP_NATIVE_POSTCONDITION_FAILED')
            row['state'] = 'PREPARED'
            self._publish(operations, owner, 'swap-prepare-done:' + command.operation_id)
            return self._receipt(row)
        except BaseException:
            self.store._uncertain = True
            raise CommitUnknown('SWAP_PREPARATION_UNKNOWN') from None

    def _resume_prepare(self, operation_id, owner):
        meta = self._guard(owner)
        operations = thaw(meta.resources.get('swap_operations', {}))
        row = operations.get(operation_id)
        if row is None or row['owner'] != owner.json():
            raise StorageError('SWAP_ORIGINAL_OPERATION_REQUIRED')
        self._receipt(row)
        if row['state'] != 'PREPARING':
            return self._receipt(row, True)
        command, permit = PrepareSwap.from_json(row['command']), _permit(row['permit'])
        config, before, current = command.config(), row['before'], self._image()
        own = current['records'].get(config.id)
        entries, original = current['actions'], before['actions']
        expected_claim = _storage_json({k: str(v) if type(v) is Decimal else v for k, v in asdict(CapitalClaim.swap(permit)).items()})
        claims = self._claims()
        if (row['boundary_consumed'] or {k: v for k, v in current['records'].items() if k != config.id} != before['records']
                or {k: v for k, v in entries['actions'].items() if k != config.id} != original['actions']
                or {k: v for k, v in claims.items() if k != config.id} != before['claims']
                or claims.get(config.id) not in (None, expected_claim)
                or entries['actions'].get(config.id) not in (None, _storage_json(self.sender._entry(config, permit)))
                or entries['last_checked_ms'] is not None and (
                    entries['last_checked_ms'] > self.sender.clock_ms()
                    or original['last_checked_ms'] is not None and entries['last_checked_ms'] < original['last_checked_ms'])):
            raise StorageError('SWAP_PREPARE_RECOVERY_NATIVE_MISMATCH')
        if own is not None:
            record = self.sender.wal.get(config.id)
            if (record.state != 'PREPARED' or record.cancel_requested or record.exchange_order_id is not None
                    or (record.client_order_id, record.session_id, record.epoch, record.reservation_id,
                        record.slot_market, record.slot_side, record.slot_level) != (
                        permit.client_order_id, permit.session_id, permit.epoch, permit.reservation_id,
                        permit.instrument, config.side.name, int(config.level_id))):
                raise StorageError('SWAP_PREPARE_RECOVERY_NATIVE_MISMATCH')
        if config.id not in claims:
            if own is not None or config.id in entries['actions']:
                raise StorageError('SWAP_PREPARE_RECOVERY_NATIVE_MISMATCH')
            row.update(state='REFUSED', reason='SWAP_NO_PREPARATION_EFFECTS')
            self._publish(operations, owner, 'swap-prepare-refused:' + operation_id)
            return self._receipt(row)
        try:
            if own is None:
                self.sender.wal.begin(config.id, client_order_id=permit.client_order_id, session_id=permit.session_id,
                                      epoch=permit.epoch, reservation_id=permit.reservation_id,
                                      slot_market=permit.instrument, slot_side=config.side.name, slot_level=int(config.level_id))
            if config.id not in entries['actions']:
                with self.sender.journal.locked() as state:
                    state['actions'][config.id] = self.sender._entry(config, permit)
                    self.sender.journal.commit(state)
            self._verify_prepared(row)
            row['state'] = 'PREPARED'
            self._publish(operations, owner, 'swap-prepare-repaired:' + operation_id)
            return self._receipt(row)
        except BaseException:
            self.store._uncertain = True
            raise CommitUnknown('SWAP_PREPARATION_RECOVERY_UNKNOWN') from None

    def _verify_prepared(self, row):
        command, permit = PrepareSwap.from_json(row['command']), _permit(row['permit'])
        config = command.config()
        record = self.sender.wal.get(config.id)
        if ((record.client_order_id, record.session_id, record.epoch, record.reservation_id,
             record.slot_market, record.slot_side, record.slot_level) != (
                permit.client_order_id, permit.session_id, permit.epoch, permit.reservation_id,
                permit.instrument, config.side.name, int(config.level_id))
                or self.sender.journal._read()['actions'].get(config.id) != self.sender._entry(config, permit)
                or self.joint._recovery.capital.claims(include_settled=True).get(config.id) != CapitalClaim.swap(permit)):
            raise StorageError('SWAP_NATIVE_POSTCONDITION_FAILED')
        return command, config, permit, record

    def _step(self, operation_id, owner, kind, cut=None, exchange_id=None, wire=None):
        meta = self._guard(owner, admission=kind in ('ARM', 'BOUNDARY'))
        operations = thaw(meta.resources.get('swap_operations', {}))
        row = operations.get(operation_id)
        if row is None or row['owner'] != owner.json():
            raise StorageError('SWAP_ORIGINAL_OPERATION_REQUIRED')
        self._receipt(row)
        command, config, permit, record = self._verify_prepared(row)
        continuing_ack = kind == 'ACK' and row['state'] == 'ACKING'
        if kind == 'ACK':
            if type(exchange_id) is not str or re.fullmatch(r'[0-9]{1,64}', exchange_id) is None:
                raise StorageError('SWAP_ACK_INVALID')
            if not self.store._created_offline and row['state'] not in ('ACKING', 'ACKED'):
                raise StorageError('SWAP_ORIGINAL_ACK_REQUIRED')
            if not row['boundary_consumed'] or row['state'] not in ('SEND_UNKNOWN', 'ACKING', 'ACKED'):
                raise StorageError('SWAP_ACK_WITHOUT_BOUNDARY')
            if row['exchange_order_id'] not in (None, exchange_id):
                raise StorageConflict('SWAP_ACK_CONFLICT')
            if any(r.intent_id != config.id and r.exchange_order_id == exchange_id for r in self.sender.wal.all_records()):
                raise StorageConflict('SWAP_EXCHANGE_ID_REUSED')
            if row['state'] == 'ACKED':
                if record.exchange_order_id != exchange_id or record.state not in ('ACKED', 'TERMINAL'):
                    raise StorageError('SWAP_ACK_POSTCONDITION_FAILED')
                return self._receipt(row, True)
            if record.state not in ('SEND_UNKNOWN', 'ACKED') or record.exchange_order_id not in (None, exchange_id):
                raise StorageError('SWAP_ACK_NATIVE_CONFLICT')
            if continuing_ack:
                before, current = row['ack_before'], self._image()
                expected = {**before['records'][config.id], 'state': 'ACKED', 'exchange_order_id': exchange_id}
                if (current['records'][config.id] not in (before['records'][config.id], expected)
                        or {k: v for k, v in current['records'].items() if k != config.id}
                        != {k: v for k, v in before['records'].items() if k != config.id}
                        or current['actions'] != before['actions'] or self._claims() != before['claims']):
                    raise StorageError('SWAP_ACK_RECOVERY_NATIVE_MISMATCH')
            else:
                row['ack_before'] = {**self._image(), 'claims': self._claims()}
            row.update(state='ACKING', exchange_order_id=exchange_id)
        elif kind == 'ARM':
            if row['state'] != 'PREPARED' or record.state != 'PREPARED':
                raise StorageError('SWAP_RECONCILE_BEFORE_RETRY')
            if self._cut() != cut:
                raise StorageConflict('SWAP_CUT_STALE')
            self.sender._check(config, permit)
            row['arm_before'] = {**self._image(), 'claims': self._claims()}
            row['state'] = 'ARMING'
        else:
            if row['state'] != 'SEND_UNKNOWN' or record.state != 'SEND_UNKNOWN' or row['boundary_consumed']:
                raise StorageError('SWAP_RECONCILE_BEFORE_RETRY')
            if not wire_matches(wire, config, permit):
                raise StorageError('SWAP_WIRE_CHANGED')
            observation = self.sender._check(config, permit)
            recovery = self.joint._recovery
            proof = recovery._fresh()
            row['boundary_checked_at_ms'] = self.sender.clock_ms()
            row['boundary_valid_until_ms'] = min(observation.observed_at_ms + self.sender.max_age_ms,
                                                 proof['bundle']['observed_at_ms'] + recovery.max_age_ms)
            row['boundary_consumed'] = True
        try:
            if not continuing_ack:
                self._publish(operations, owner, 'swap-' + kind.lower() + '-start:' + operation_id)
            if kind == 'BOUNDARY':
                self.sender.request_budget.charge('CREATE', 'swap:' + permit.client_order_id,
                                                  slot=f'{permit.instrument}:{config.side.name}:{config.level_id}')
            if kind == 'ARM':
                self.sender.wal.arm_send(config.id, client_order_id=permit.client_order_id,
                                         session_id=permit.session_id, epoch=permit.epoch, reservation_id=permit.reservation_id)
                row['state'] = 'SEND_UNKNOWN'
            elif kind == 'ACK':
                self.sender.wal.acknowledge(config.id, exchange_id)
                row['state'] = 'ACKED'
            verified = self._verify_prepared(row)[3]
            if (kind == 'ARM' and verified.state != 'SEND_UNKNOWN'
                    or kind == 'ACK' and (verified.state != 'ACKED' or verified.exchange_order_id != exchange_id)):
                raise StorageError('SWAP_NATIVE_POSTCONDITION_FAILED')
            self._publish(operations, owner, 'swap-' + kind.lower() + '-done:' + operation_id)
            return self._receipt(row)
        except BaseException:
            self.store._uncertain = True
            raise CommitUnknown('SWAP_CHECKPOINT_UNKNOWN') from None

    async def _call(self, function, *args):
        try:
            return await self.store._call(function, *args)
        except asyncio.CancelledError:
            self.store._uncertain = True
            self.joint.revoke()
            raise

    async def prepare(self, command, *, cut, owner):
        if type(command) is not PrepareSwap or type(cut) is not ProductCut or type(owner) is not OwnerToken:
            raise StorageError('SWAP_COMMAND_INVALID')
        self.store._assert_binding(cut.checkpoint.binding)
        return await self._call(self._prepare, command, cut, owner)

    async def arm(self, operation_id, *, cut, owner):
        if type(cut) is not ProductCut or type(owner) is not OwnerToken:
            raise StorageError('SWAP_COMMAND_INVALID')
        identifier(operation_id)
        self.store._assert_binding(cut.checkpoint.binding)
        return await self._call(self._step, operation_id, owner, 'ARM', cut)

    async def authorize_boundary(self, operation_id, *, wire_payload, owner):
        identifier(operation_id)
        if type(owner) is not OwnerToken or type(wire_payload) is not bytes or len(wire_payload) > 4096:
            raise StorageError('SWAP_WIRE_INVALID')
        try:
            wire = json.loads(wire_payload)
            if type(wire) is not dict or encode(wire) != wire_payload:
                raise ValueError
        except (ValueError, TypeError):
            raise StorageError('SWAP_WIRE_INVALID') from None
        return await self._call(self._step, operation_id, owner, 'BOUNDARY', None, None, wire)

    async def acknowledge(self, operation_id, *, exchange_order_id, owner):
        identifier(operation_id)
        if type(owner) is not OwnerToken:
            raise StorageError('SWAP_COMMAND_INVALID')
        return await self._call(self._step, operation_id, owner, 'ACK', None, exchange_order_id)

    async def resume_prepare(self, operation_id, *, owner):
        identifier(operation_id)
        if type(owner) is not OwnerToken:
            raise StorageError('SWAP_COMMAND_INVALID')
        return await self._call(self._resume_prepare, operation_id, owner)

    def _resume_arm(self, operation_id, owner):
        meta = self._guard(owner)
        operations = thaw(meta.resources.get('swap_operations', {}))
        row = operations.get(operation_id)
        if row is None or row['owner'] != owner.json():
            raise StorageError('SWAP_ORIGINAL_OPERATION_REQUIRED')
        self._receipt(row)
        if row['state'] != 'ARMING':
            return self._receipt(row, True)
        _, _, permit, record = self._verify_prepared(row)
        before, current = row['arm_before'], self._image()
        expected = {**before['records'][record.intent_id], 'state': 'SEND_UNKNOWN'}
        if (current['records'][record.intent_id] not in (before['records'][record.intent_id], expected)
                or {k: v for k, v in current['records'].items() if k != record.intent_id}
                != {k: v for k, v in before['records'].items() if k != record.intent_id}
                or current['actions'] != before['actions'] or self._claims() != before['claims']):
            raise StorageError('SWAP_ARM_RECOVERY_NATIVE_MISMATCH')
        try:
            if record.state == 'PREPARED':
                self.sender.wal.arm_send(record.intent_id, client_order_id=permit.client_order_id,
                                         session_id=permit.session_id, epoch=permit.epoch, reservation_id=permit.reservation_id)
            elif record.state != 'SEND_UNKNOWN':
                raise StorageError('SWAP_ARM_RECOVERY_NATIVE_MISMATCH')
            if self._verify_prepared(row)[3].state != 'SEND_UNKNOWN':
                raise StorageError('SWAP_ARM_RECOVERY_NATIVE_MISMATCH')
            row['state'] = 'SEND_UNKNOWN'
            self._publish(operations, owner, 'swap-arm-repaired:' + operation_id)
            # No volatile permit/action cache is restored by a recovery receipt.
            return self._receipt(row)
        except BaseException:
            self.store._uncertain = True
            raise CommitUnknown('SWAP_ARM_RECOVERY_UNKNOWN') from None

    async def resume_arm(self, operation_id, *, owner):
        identifier(operation_id)
        if type(owner) is not OwnerToken:
            raise StorageError('SWAP_COMMAND_INVALID')
        return await self._call(self._resume_arm, operation_id, owner)

    def _lookup(self, operation_id):
        meta = self._guard()
        row = meta.resources.get('swap_operations', {}).get(operation_id)
        return None if row is None else self._receipt(row, True)

    async def lookup(self, operation_id, binding):
        identifier(operation_id)
        self.store._assert_binding(binding)
        return await self._call(self._lookup, operation_id)


def expected_wire(config, permit):
    result = {'clOrdId': permit.client_order_id, 'tdMode': 'cross', 'ordType': 'post_only',
              'instId': permit.instrument, 'side': config.side.name.lower(),
              'sz': str(permit.contracts), 'px': str(config.price)}
    if permit.position_mode is PositionMode.ONEWAY:
        result['posSide'] = 'net'
        if config.position_action is PositionAction.CLOSE:
            result['reduceOnly'] = True
    else:
        result['posSide'] = ('long' if config.side is TradeType.BUY else 'short') if (
            config.position_action is PositionAction.OPEN) else ('short' if config.side is TradeType.BUY else 'long')
    return result


async def open_file_swap(joint):
    repo = FileSwapRepository(joint)
    await repo._call(repo._initialize)
    repo.sender._storage_repository = repo
    return repo


def wire_matches(wire, config, permit):
    try:
        actual, expected = dict(wire), expected_wire(config, permit)
        for key in ('px', 'sz'):
            if type(actual[key]) is not str:
                return False
            actual[key] = signed_decimal(Decimal(actual[key]))
            expected[key] = Decimal(expected[key])
        return (actual == expected and ('reduceOnly' not in actual or type(actual['reduceOnly']) is bool))
    except (KeyError, TypeError, ValueError, ArithmeticError, StorageError):
        return False
