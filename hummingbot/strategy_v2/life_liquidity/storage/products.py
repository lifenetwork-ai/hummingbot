"""Product ports preserve the existing native units and conservative economics.

File commands are recoverable sagas, never account-atomic transactions or send
grants. A completed receipt can be replayed; an interrupted command is retained
and denies new writes. Native imports/release require separate qualified recovery.
"""
import asyncio
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.accounting import CapitalMeasurement
from hummingbot.strategy_v2.life_liquidity.carry_monitor import CarryDecision, CarryMonitor
from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeCoordinator
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim, CapitalDecision, SharedCapitalAuthority
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerMode,
    OwnerToken,
    StateBinding,
    StateCut,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    digest,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import decimal, signed_decimal


def _json(value):
    if isinstance(value, Decimal):
        return str(signed_decimal(value))
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json(item) for item in value]
    return value


@dataclass(frozen=True)
class ProductCut:
    checkpoint: StateCut
    components: tuple[tuple[str, str], ...]

    def __post_init__(self):
        if (not isinstance(self.checkpoint, StateCut) or type(self.components) is not tuple
                or any(type(item) is not tuple or len(item) != 2 for item in self.components)
                or len({item[0] for item in self.components}) != len(self.components)):
            raise StorageError('PRODUCT_CUT_INVALID')
        for key, value in self.components:
            identifier(key)
            digest(value)

    def json(self):
        return {'checkpoint': self.checkpoint.json(), 'components': dict(self.components)}


@dataclass(frozen=True)
class ReserveCapital:
    operation_id: str
    claim: CapitalClaim  # quantity_base=LIFE; prices/margin/fees/stress=USDT, not contracts.

    def __post_init__(self):
        identifier(self.operation_id)
        if len(self.operation_id) > 96 or not isinstance(self.claim, CapitalClaim):
            raise StorageError('PRODUCT_COMMAND_INVALID')

    def json(self):
        return {'kind': 'RESERVE_CAPITAL', 'operation_id': self.operation_id, 'claim': _json(asdict(self.claim))}


@dataclass(frozen=True)
class UpdateHedgeTarget:
    operation_id: str
    expected_version: int
    target_base_life: Decimal

    def __post_init__(self):
        identifier(self.operation_id)
        if len(self.operation_id) > 96 or type(self.expected_version) is not int or self.expected_version < 0:
            raise StorageError('PRODUCT_COMMAND_INVALID')
        object.__setattr__(self, 'target_base_life', decimal(self.target_base_life))

    def json(self):
        return {'kind': 'HEDGE_TARGET', 'operation_id': self.operation_id,
                'expected_version': self.expected_version, 'target_base_life': str(self.target_base_life)}


@dataclass(frozen=True)
class ProductReceipt:
    binding: StateBinding
    operation_id: str
    decision: CapitalDecision | None
    changed: bool
    replayed: bool
    atomic_scope: AtomicScope = AtomicScope.RECOVERABLE_SAGA


class SharedCapitalRepository(Protocol):
    async def cut(self, binding: StateBinding) -> ProductCut:
        ...

    async def claims(self, binding: StateBinding) -> tuple[CapitalClaim, ...]:
        ...

    async def check_capital(self, binding: StateBinding) -> CapitalDecision:
        ...

    async def reserve(self, command: ReserveCapital, cut: ProductCut, owner: OwnerToken) -> ProductReceipt:
        ...


class CarryRepository(Protocol):
    async def check_carry(self, binding: StateBinding, prospective: CapitalClaim | None = None) -> CarryDecision:
        ...


class HedgeRepository(Protocol):
    async def update_target(self, command: UpdateHedgeTarget, cut: ProductCut, owner: OwnerToken) -> ProductReceipt:
        ...

    async def hedge_allows_spot(self, binding: StateBinding) -> bool:
        ...


class RiskRepository(Protocol):
    async def measure_capital(self, binding: StateBinding) -> CapitalMeasurement | None:
        ...

    async def evaluate_markout(self, binding: StateBinding) -> bool:
        ...


class FileProductRepository:
    """Native objects are private to the adapter; every call uses the store worker.

    Policy and actual native checkpoints participate in each logical cut. No
    native path, private cache or mutable native object is returned to consumers.
    Diagnostic reads after a partial command cannot authorize another command.
    """
    atomic_scope = AtomicScope.RECOVERABLE_SAGA

    def __init__(self, store, capital, carry, hedge, risk, markout):
        self.store, self.binding = store, store.binding
        self._capital, self._carry, self._hedge, self._risk, self._markout = capital, carry, hedge, risk, markout
        self._components = {key: value for key, value in (
            ('capital', capital), ('carry', carry), ('hedge', hedge), ('risk', risk), ('markout', markout))
            if value is not None}

    def _policies(self):
        result = {}
        for key, component in self._components.items():
            journal = getattr(component, 'journal', None)
            # File policy encoding is contained in the adapter, not in callers.
            policy = ({'journal': journal.policy, 'configured': _json(asdict(component.policy))}
                      if journal is not None else component._policy())
            result[key] = fingerprint(policy)
        return result

    def validate_controller(self, controller):
        """Bind the actual installed objects; a second native cache is rejected."""
        expected = {'capital': controller._shared_capital_authority, 'carry': controller._carry_monitor,
                    'hedge': controller._hedge_coordinator, 'risk': controller._capital_risk_monitor,
                    'markout': controller._markout_monitor}
        if (controller.config.storage_binding != self.binding
                or any(component is not expected[key] for key, component in self._components.items())
                or any(value is not None and key not in self._components for key, value in expected.items())):
            raise StorageError('PRODUCT_CONTROLLER_BINDING_MISMATCH')

    def _initialize(self):
        if self._capital is not None and (not isinstance(self._capital, SharedCapitalAuthority)
                                          or self._capital.policy.account_uid != self.binding.account_uid):
            raise StorageError('PRODUCT_ACCOUNT_OR_TYPE_MISMATCH')
        if self._carry is not None and (not isinstance(self._carry, CarryMonitor)
                                        or self._carry.capital is not self._capital):
            raise StorageError('PRODUCT_CAPITAL_BINDING_MISMATCH')
        if self._hedge is not None and (not isinstance(self._hedge, HedgeCoordinator)
                                        or self._hedge.capital is not self._capital):
            raise StorageError('PRODUCT_CAPITAL_BINDING_MISMATCH')
        from hummingbot.strategy_v2.life_liquidity.capital_risk import CapitalRiskMonitor
        from hummingbot.strategy_v2.life_liquidity.markout_risk import ReconciledMarkoutMonitor
        if (self._risk is not None and not isinstance(self._risk, CapitalRiskMonitor)
                or self._markout is not None and not isinstance(self._markout, ReconciledMarkoutMonitor)):
            raise StorageError('PRODUCT_RISK_BINDING_MISMATCH')
        meta = self.store._read()
        policy = self._policies()
        previous = meta.resources.get('product_policy')
        if previous is not None:
            if thaw(previous) != policy:
                raise StorageError('PRODUCT_POLICY_MISMATCH')
        else:
            if (meta.ownership.mode is not OwnerMode.ACTIVE or self.binding.environment != 'OFFLINE_SIMULATION'
                    or getattr(self.store, '_cold_unarmed', False)):
                raise StorageError('PRODUCT_BOOTSTRAP_OFFLINE_ONLY')
            if self._capital is not None and self._capital.claim_ids():
                raise StorageError('PRODUCT_NATIVE_IMPORT_REQUIRED')
            self.store._commit(meta.cut, meta.ownership.token, 'products:bind', (
                ('product_policy', encode(policy)), ('product_operations', encode({}))))
        self._native_cuts()

    def _native_cuts(self):
        if thaw(self.store._read().resources['product_policy']) != self._policies():
            raise StorageError('PRODUCT_POLICY_MISMATCH')
        result = []
        for key, component in sorted(self._components.items()):
            journal = getattr(component, 'journal', None)
            if journal is not None:
                with journal.inspect() as state:
                    result.append((key, fingerprint(state)))
            else:
                # These monitors independently verify disk against their memory.
                if not component._verified():
                    raise StorageUnavailable('PRODUCT_NATIVE_UNVERIFIED')
                result.append((key, fingerprint(component._state)))
        return tuple(result)

    def _cut(self):
        return ProductCut(self.store._read().cut, self._native_cuts())

    async def cut(self, binding):
        self.store._assert_binding(binding)
        return await self.store._call(self._cut)

    def _read_component(self, name, method, *args):
        self._native_cuts()
        component = self._components.get(name)
        if component is None:
            raise StorageError('PRODUCT_ROUTE_NOT_INSTALLED')
        result = getattr(component, method)(*args)
        self._native_cuts()  # Native writes/reads must leave verified state.
        return result

    async def _read(self, binding, name, method, *args):
        self.store._assert_binding(binding)
        return await self.store._call(self._read_component, name, method, *args)

    async def claims(self, binding):
        values = await self._read(binding, 'capital', 'claims')
        return tuple(values[key] for key in sorted(values))

    async def check_capital(self, binding):
        return await self._read(binding, 'capital', 'check')

    async def check_carry(self, binding, prospective=None):
        if prospective is not None and (not isinstance(prospective, CapitalClaim)
                                        or prospective.account_uid != self.binding.account_uid):
            raise StorageError('PRODUCT_ACCOUNT_OR_TYPE_MISMATCH')
        return await self._read(binding, 'carry', 'check', prospective)

    async def hedge_allows_spot(self, binding):
        return await self._read(binding, 'hedge', 'allows_spot')

    async def measure_capital(self, binding):
        return await self._read(binding, 'risk', 'measure')

    async def evaluate_markout(self, binding):
        return await self._read(binding, 'markout', 'evaluate')

    def _receipt(self, operation, row, replayed):
        raw = row['result']
        decision = None if raw['decision'] is None else CapitalDecision(**{
            key: Decimal(value) if key not in ('allowed', 'reason_code') else value
            for key, value in raw['decision'].items()})
        return ProductReceipt(self.binding, operation, decision, raw['changed'], replayed)

    def _operate(self, command, cut, owner):
        if not isinstance(cut, ProductCut) or not isinstance(owner, OwnerToken):
            raise StorageError('PRODUCT_COMMAND_INVALID')
        self.store._assert_binding(cut.checkpoint.binding)
        meta = self.store._read()
        self._native_cuts()
        payload = command.json()
        identity = fingerprint({'command': payload, 'cut': cut.json(), 'owner': owner.json()})
        operations = thaw(meta.resources['product_operations'])
        row = operations.get(command.operation_id)
        if row is not None:
            if row['identity'] != identity:
                raise StorageConflict('PRODUCT_OPERATION_CONFLICT')
            if row['state'] != 'APPLIED':
                raise StorageUnavailable('PRODUCT_OPERATION_REQUIRES_RECOVERY')
            return self._receipt(command.operation_id, row, True)
        now = utc(self.store._clock())
        if (meta.ownership.mode is not OwnerMode.ACTIVE or meta.ownership.token != owner
                or not owner.valid_from <= now < owner.expires_at or getattr(self.store, '_cold_unarmed', False)
                or not getattr(self.store, '_created_offline', False)
                and getattr(self.store, '_admission_generation', None) != owner.generation):
            raise StorageError('PRODUCT_OWNER_REVOKED_OR_EXPIRED')
        if any(item['state'] != 'APPLIED' for item in operations.values()):
            raise StorageUnavailable('PRODUCT_OPERATION_REQUIRES_RECOVERY')
        if any(row['state'] != 'APPLIED' for name in ('recorder_outbox', 'recorder_progress', 'recorder_positions', 'recorder_events', 'recorder_configurations')
               for row in meta.resources.get(name, {}).values()):
            raise StorageUnavailable('PRODUCT_REPORTING_REPAIR_REQUIRED')
        if hasattr(self.store, '_incomplete') and self.store._incomplete(meta.resources):
            raise StorageUnavailable('PRODUCT_FINANCIAL_RECOVERY_REQUIRED')
        if self._cut() != cut:
            raise StorageConflict('PRODUCT_CUT_STALE')
        if isinstance(command, ReserveCapital):
            claim = command.claim
            if (claim.account_uid != self.binding.account_uid
                    or (claim.session_id, claim.epoch) != (str(owner.session_id), owner.epoch)):
                raise StorageError('PRODUCT_SESSION_MISMATCH')
            if self._capital is None:
                raise StorageError('PRODUCT_ROUTE_NOT_INSTALLED')
            known = [value for value in operations.values()
                     if value['payload']['kind'] == 'RESERVE_CAPITAL'
                     and value['payload']['claim']['intent_id'] == claim.intent_id]
            if any(value['payload']['claim'] != payload['claim'] or value['owner'] != owner.json()
                   for value in known):
                raise StorageConflict('PRODUCT_INTENT_IDENTITY_CONFLICT')
            existing = self._capital.claims().get(claim.intent_id)
            if existing is not None and not known:
                raise StorageError('PRODUCT_NATIVE_IMPORT_REQUIRED')
        elif self._hedge is None:
            raise StorageError('PRODUCT_ROUTE_NOT_INSTALLED')
        else:
            try:
                changed = self._hedge.validate_target(command.operation_id,
                                                      expected_version=command.expected_version,
                                                      target_base=command.target_base_life)
            except ValueError as exc:
                raise StorageError(str(exc) if str(exc).startswith('HEDGE_')
                                   else 'PRODUCT_NATIVE_UNVERIFIED') from None
            if not changed:
                # An unrecorded native update is an import, not a new command.
                raise StorageError('PRODUCT_NATIVE_IMPORT_REQUIRED')
        row = {'identity': identity, 'state': 'APPLYING', 'payload': payload,
               'owner': owner.json(), 'cut': cut.json(), 'result': None}
        operations[command.operation_id] = row
        self.store._commit(meta.cut, owner, 'product-start:' + command.operation_id,
                           (('product_operations', encode(operations)),))
        try:
            if isinstance(command, ReserveCapital):
                decision = self._capital.reserve(command.claim)
                if decision.allowed and self._capital.claims().get(command.claim.intent_id) != command.claim:
                    raise StorageUnavailable('PRODUCT_NATIVE_POSTCONDITION_FAILED')
                result = {'decision': _json(asdict(decision)), 'changed': decision.allowed and existing is None}
            else:
                changed = self._hedge.update_target(command.operation_id, expected_version=command.expected_version,
                                                    target_base=command.target_base_life)
                with self._hedge.journal.inspect() as state:
                    if state['updates'].get(command.operation_id) != {
                            'expected_version': command.expected_version, 'target': str(command.target_base_life)}:
                        raise StorageUnavailable('PRODUCT_NATIVE_POSTCONDITION_FAILED')
                result = {'decision': None, 'changed': changed}
            self._native_cuts()
            row.update(state='APPLIED', result=result)
            meta = self.store._read()
            # Same-writer completion may record an admitted result after expiry;
            # it does not create fresh admission or release an economic hold.
            self.store._commit(meta.cut, owner, 'product-done:' + command.operation_id,
                               (('product_operations', encode(operations)),), observation=True)
            return self._receipt(command.operation_id, row, False)
        except BaseException:
            self.store._uncertain = True
            raise

    async def _write(self, command, cut, owner):
        try:
            return await self.store._call(self._operate, command, cut, owner)
        except asyncio.CancelledError:
            self.store._uncertain = True
            raise

    async def reserve(self, command, cut, owner):
        if not isinstance(command, ReserveCapital):
            raise StorageError('PRODUCT_COMMAND_INVALID')
        return await self._write(command, cut, owner)

    async def update_target(self, command, cut, owner):
        if not isinstance(command, UpdateHedgeTarget):
            raise StorageError('PRODUCT_COMMAND_INVALID')
        return await self._write(command, cut, owner)


async def open_file_products(store, *, capital=None, carry=None, hedge=None, risk=None, markout=None):
    from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore
    if not isinstance(store, FileCheckpointStore):
        raise StorageError('PRODUCT_FILE_STORE_REQUIRED')
    repo = FileProductRepository(store, capital, carry, hedge, risk, markout)
    await store._call(repo._initialize)
    return repo
