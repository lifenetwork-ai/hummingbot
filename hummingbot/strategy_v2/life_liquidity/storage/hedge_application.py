"""Worker-owned hedge planning over the same SWAP reservation and send ports."""
import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeCoordinator, HedgeMarket
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    encode,
    fingerprint,
    identifier,
    thaw,
)
from hummingbot.strategy_v2.life_liquidity.storage.swap import PrepareSwap, _storage_json
from hummingbot.strategy_v2.life_liquidity.storage.swap_application import FileSwapApplication
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction


@dataclass(frozen=True)
class HedgePlan:
    operation_id: str
    market_payload: bytes

    def __post_init__(self):
        identifier(self.operation_id)
        if (len(self.operation_id) > 96 or type(self.market_payload) is not bytes
                or len(self.market_payload) > 65536):
            raise StorageError('HEDGE_COMMAND_INVALID')
        m = self.market()
        if (len(m.bids) > 64 or len(m.asks) > 64
                or encode(_storage_json(asdict(m))) != self.market_payload):
            raise StorageError('HEDGE_COMMAND_INVALID')

    def market(self):
        try:
            data = json.loads(self.market_payload)
            for key in ('bids', 'asks'):
                data[key] = tuple(tuple(Decimal(v) for v in row) for row in data[key])
            for key in ('fee_rate', 'funding_cost_quote', 'available_edge_quote'):
                data[key] = Decimal(data[key])
            return HedgeMarket(**data)
        except (TypeError, ValueError, KeyError, ArithmeticError):
            raise StorageError('HEDGE_COMMAND_INVALID') from None

    def json(self):
        return {'operation_id': self.operation_id, 'market': json.loads(self.market_payload)}

    @classmethod
    def capture(cls, operation_id, market):
        if type(market) is not HedgeMarket:
            raise StorageError('HEDGE_COMMAND_INVALID')
        return cls(operation_id, encode(_storage_json(asdict(market))))


@dataclass(frozen=True)
class HedgePlanResult:
    operation_id: str
    children: tuple[PrepareSwap, ...]
    stops: tuple[str, ...]
    reason_code: str
    replayed: bool


class FileHedgeApplication:
    def __init__(self, swap, coordinator):
        if (type(swap) is not FileSwapApplication or type(coordinator) is not HedgeCoordinator
                or coordinator.controller is not swap.controller or coordinator.sender is not swap.sender
                or coordinator.capital is not swap.repository.joint._recovery.capital
                or getattr(coordinator, '_storage_application', None) is not None):
            raise StorageError('HEDGE_APPLICATION_BINDING_INVALID')
        self.swap, self.repository, self.store = swap, swap.repository, swap.store
        self.native, self.owner = coordinator, swap.owner
        self._planning = None
        self._lock = asyncio.Lock()
        self._admitted = 0

    @classmethod
    async def install(cls, swap, coordinator):
        app = cls(swap, coordinator)
        await app.repository._call(app._initialize)
        coordinator._storage_application = app
        app.repository._hedge_application = app
        return app

    def _policy(self):
        return fingerprint(self.native._binding)

    def _guard(self):
        meta = self.repository._guard(self.owner)
        if (self.native is not self.swap.controller._hedge_coordinator
                or meta.resources.get('hedge_application_policy') != self._policy()):
            raise StorageError('HEDGE_APPLICATION_POLICY_MISMATCH')
        self.native._validate_state(self.native.journal._read())
        return meta

    def _initialize(self):
        meta = self.repository._guard(self.owner)
        previous = meta.resources.get('hedge_application_policy')
        if previous is None:
            if not self.store._created_offline or self.native.journal._read()['orders']:
                raise StorageError('HEDGE_APPLICATION_IMPORT_REQUIRED')
            self.store._commit(meta.cut, self.owner, 'hedge:bind', (
                ('hedge_application_policy', encode(self._policy())), ('hedge_plans', encode({}))))
        elif previous != self._policy():
            raise StorageError('HEDGE_APPLICATION_POLICY_MISMATCH')
        if not self.store._created_offline:
            self.native._runtime_enabled = False

    def _publish(self, rows, phase, operation):
        meta = self.store._read()
        self.store._commit(meta.cut, self.owner, 'hedge-' + phase + ':' + operation,
                           (('hedge_plans', encode(rows)),), observation=True)

    def _verify_origin(self, row):
        if (row['owner'] != self.owner.json() or row['identity'] != fingerprint({
                'command': row['command'], 'owner': row['owner'], 'cut': row['cut'], 'before': row['before']})):
            raise StorageError('HEDGE_PLAN_ORIGIN_UNVERIFIED')

    def prepare_child(self, config):
        self.store.assert_worker_thread()
        if self._planning is None:
            raise StorageError('HEDGE_PLAN_CONTEXT_REQUIRED')
        rows = thaw(self._guard().resources['hedge_plans'])
        row = rows[self._planning]
        if row['child'] is not None:
            raise StorageError('HEDGE_MULTIPLE_CHILDREN_DENIED')
        command = PrepareSwap.from_config('hedge:' + config.id, 'HBOT' + uuid.uuid4().hex[:28], config)
        row['child'] = command.json()
        row['child_before'] = _storage_json(self.native.journal._read()['orders'][config.id])
        self._publish(rows, 'child', self._planning)
        receipt = self.repository._prepare(command, self.repository._cut(), self.owner)
        if receipt.state != 'PREPARED':
            raise StorageError('HEDGE_CHILD_PREPARATION_INCOMPLETE')
        return CreateExecutorAction(controller_id=self.swap.controller.config.id, executor_config=command.config())

    def _plan(self, command):
        meta = self._guard()
        rows = thaw(meta.resources['hedge_plans'])
        old = rows.get(command.operation_id)
        if old is not None:
            self._verify_origin(old)
            if old['command'] != command.json():
                raise StorageConflict('HEDGE_PLAN_CONFLICT')
            if old['state'] != 'PLANNED':
                raise StorageUnavailable('HEDGE_PLAN_RECOVERY_REQUIRED')
            return HedgePlanResult(command.operation_id, (), (), old['reason'], True)
        self.repository._guard(self.owner, admission=True)
        if any(row['state'] != 'PLANNED' for row in rows.values()):
            raise StorageUnavailable('HEDGE_PLAN_RECOVERY_REQUIRED')
        before = _storage_json(self.native.journal._read())
        origin = {'command': command.json(), 'owner': self.owner.json(), 'cut': self.repository._cut().json(), 'before': before}
        rows[command.operation_id] = {**origin, 'identity': fingerprint(origin), 'state': 'PLANNING',
                                      'child': None, 'child_before': None, 'reason': None, 'stops': []}
        self._publish(rows, 'start', command.operation_id)
        market = self.native.market
        self._planning = command.operation_id
        self.native.market = command.market
        try:
            actions = self.native.propose()
            if self.store._uncertain:
                raise StorageUnavailable('HEDGE_CHILD_UNKNOWN')
            rows = thaw(self._guard().resources['hedge_plans'])
            row = rows[command.operation_id]
            children = tuple(a for a in actions if isinstance(a, CreateExecutorAction))
            stops = tuple(a.executor_id for a in actions if isinstance(a, StopExecutorAction))
            if len(children) > 1 or len(stops) > self.store.limits.max_operations:
                raise StorageError('HEDGE_ACTION_SCOPE_INVALID')
            command_child = None if row['child'] is None else PrepareSwap.from_json(row['child'])
            if children:
                if command_child is None or children[0].executor_config != command_child.config():
                    raise StorageError('HEDGE_CHILD_ORIGIN_MISMATCH')
                self.repository._verify_prepared(thaw(self.store._read().resources['swap_operations'][command_child.operation_id]))
                if self.native.journal._read()['orders'][command_child.config().id]['status'] != 'ISSUED':
                    raise StorageError('HEDGE_CHILD_NOT_ISSUED')
            elif command_child is not None or set(self.native.journal._read()['orders']) != set(before['orders']):
                raise StorageUnavailable('HEDGE_CHILD_RECOVERY_REQUIRED')
            row.update(state='PLANNED', reason=self.native.reason_code, stops=list(stops))
            self._publish(rows, 'done', command.operation_id)
            return HedgePlanResult(command.operation_id, () if command_child is None else (command_child,),
                                   stops, self.native.reason_code, False)
        except BaseException:
            self.store._uncertain = True
            raise CommitUnknown('HEDGE_PLAN_UNKNOWN') from None
        finally:
            self.native.market = market
            self._planning = None

    def _resume(self, operation_id):
        rows = thaw(self._guard().resources['hedge_plans'])
        row = rows.get(operation_id)
        if row is None:
            raise StorageError('HEDGE_ORIGINAL_PLAN_REQUIRED')
        self._verify_origin(row)
        if row['state'] == 'PLANNED':
            return HedgePlanResult(operation_id, (), (), row['reason'], True)
        before, current = row['before'], _storage_json(self.native.journal._read())
        added = set(current['orders']) - set(before['orders'])
        if (len(added) > 1 or not set(before['orders']) <= set(current['orders'])
                or any(current['orders'][k] != v for k, v in before['orders'].items())
                or any(current[k] != before[k] for k in ('target', 'target_version', 'updates', 'trades'))):
            raise StorageError('HEDGE_RECOVERY_NATIVE_MISMATCH')
        child = None if row['child'] is None else PrepareSwap.from_json(row['child'])
        if child is not None and added != {child.config().id}:
            raise StorageError('HEDGE_RECOVERY_CHILD_MISMATCH')
        if child is not None:
            entry = current['orders'][child.config().id]
            expected = row['child_before']
            if ({k: v for k, v in entry.items() if k != 'status'}
                    != {k: v for k, v in expected.items() if k != 'status'}
                    or entry['status'] not in ('PREPARING', 'ISSUED')):
                raise StorageError('HEDGE_RECOVERY_CHILD_MISMATCH')
        elif added:
            intent = next(iter(added))
            if (current['orders'][intent]['status'] != 'PREPARING'
                    or intent in self.repository._claims()
                    or any(r.intent_id == intent for r in self.swap.sender.wal.all_records())):
                raise StorageError('HEDGE_RECOVERY_UNRECORDED_EXPOSURE')
        try:
            status = 'TERMINAL'
            if child is not None:
                operation = thaw(self.store._read().resources.get('swap_operations', {}).get(child.operation_id))
                if operation is not None:
                    receipt = self.repository._resume_prepare(child.operation_id, self.owner)
                    if receipt.state == 'PREPARED':
                        self.repository._verify_prepared(thaw(self.store._read().resources['swap_operations'][child.operation_id]))
                        status = 'ISSUED'
                    elif receipt.state != 'REFUSED':
                        raise StorageError('HEDGE_RECOVERY_SEND_STATE_CHANGED')
                elif (child.config().id in self.repository._claims()
                        or any(r.intent_id == child.config().id for r in self.swap.sender.wal.all_records())):
                    raise StorageError('HEDGE_RECOVERY_UNRECORDED_EXPOSURE')
            if added:
                intent = next(iter(added))
                with self.native.journal.locked() as state:
                    self.native._validate_state(state)
                    state['orders'][intent]['status'] = status
                    self.native.journal.commit(state)
            row.update(state='PLANNED', reason='HEDGE_PLAN_RECOVERED_UNARMED', stops=[])
            self._publish(rows, 'done', operation_id)
            return HedgePlanResult(operation_id, (), (), row['reason'], False)
        except BaseException:
            self.store._uncertain = True
            raise CommitUnknown('HEDGE_RECOVERY_UNKNOWN') from None

    async def resume(self, operation_id):
        identifier(operation_id)
        return await self.repository._call(self._resume, operation_id)

    async def plan(self, operation_id, market):
        command = HedgePlan.capture(operation_id, market)
        if self._admitted >= self.store.limits.max_pending:
            raise StorageUnavailable('HEDGE_APPLICATION_BACKPRESSURE')
        self._admitted += 1
        try:
            async with self._lock:
                if not self.swap.local_ready():
                    raise StorageError('HEDGE_APPLICATION_REVOKED')
                if self.swap._proposals + len(self.swap._pending) + len(self.swap._unknown_attempts) >= self.swap.max_pending:
                    raise StorageUnavailable('HEDGE_APPLICATION_BACKPRESSURE')
                self.swap._proposals += 1
                try:
                    await self.swap._uid()
                    result = await self.repository._call(self._plan, command)
                    if result.replayed:
                        return []
                    actions = []
                    for child in result.children:
                        receipt = await self.repository.lookup(child.operation_id, self.repository.binding)
                        self.swap.adopt_prepared(child, receipt)
                        actions.append(CreateExecutorAction(controller_id=self.swap.controller.config.id,
                                                            executor_config=child.config()))
                    if not self.swap.local_ready():
                        raise StorageError('HEDGE_APPLICATION_REVOKED')
                    return [StopExecutorAction(controller_id=self.swap.controller.config.id, executor_id=k)
                            for k in result.stops] + actions
                finally:
                    self.swap._proposals -= 1
        finally:
            self._admitted -= 1
