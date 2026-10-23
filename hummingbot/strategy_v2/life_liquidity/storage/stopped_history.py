"""Committed money prefixes for cold File NAV verification, without source I/O.

Exchange event time is not publication order: a late observation must not be
inserted into a NAV that was committed before the observation reached File.
Repeated cumulative snapshots establish one first publication per economic ID.
"""
from hummingbot.strategy_v2.life_liquidity.storage.capital_replay import cashflow_from_json, replay_capital
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot
from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashSpot


class StoppedMoneyHistory:
    def __init__(self, state, *, policy, validate_cashflow, max_rows, max_replay_rows):
        self.resources, self.operations = state['resources'], state['operations']
        self.policy, self.validate_cashflow = policy, validate_cashflow
        self.max_rows, self.remaining_work = max_rows, max_replay_rows
        self.origins, self.cash_origins = {}, {}
        self.rows = 0
        revisions = [v['revision'] for v in self.operations.values()]
        if (len(revisions) > max_rows or len(revisions) != state['revision']
                or state['event_version'] != state['revision'] or len(set(revisions)) != len(revisions)
                or any(type(v) is not int or not 0 < v <= state['revision'] for v in revisions)):
            raise StorageError('STOPPED_CAPITAL_HISTORY_REVISION_INVALID')
        for name, prefix, decoder, kind in (
                ('fill_operations', 'fill-done:', FillSpot.from_json, 'fills'),
                ('cashflow_operations', 'cash-done:', cashflow_from_json, 'cashflows')):
            for key, row in self.resources.get(name, {}).items():
                command = self._command(key, row, decoder)
                revision = self.operations.get(prefix + key, {}).get('revision')
                # Joint recovery can finish these commands in its own commit.
                if revision is not None:
                    self._fill(revision, command) if kind == 'fills' else self._cash(revision, command)
        for key, row in self.resources.get('joint_cash_operations', {}).items():
            command = self._command(key, row, JointCashSpot.from_json, joint=True)
            revision = self.operations.get('joint-done:' + key, {}).get('revision')
            if revision is None:
                raise StorageError('STOPPED_CAPITAL_HISTORY_PUBLICATION_MISSING')
            for fill in command.spot_fills:
                retained = self.resources.get('fill_operations', {}).get(fill.operation_id)
                if retained is None or retained['command'] != fill.json():
                    raise StorageError('STOPPED_CAPITAL_HISTORY_COMMAND_INVALID')
                self._fill(revision, fill)
            for cash in command.spot_cashflows:
                retained = self.resources.get('cashflow_operations', {}).get(cash.operation_id)
                if retained is None or retained['command'] != cash.json():
                    raise StorageError('STOPPED_CAPITAL_HISTORY_COMMAND_INVALID')
                self._cash(revision, cash)
            for event in command.events:
                target = self.resources.get('joint_cashflows', {}).get(event.event_id)
                if target is None or target['event'] != event.json():
                    raise StorageError('STOPPED_CAPITAL_HISTORY_EVENT_CONFLICT')
                self._add(revision, 'joint_cashflows', event.event_id)
        expected = {(kind, key) for kind in ('fills', 'cashflows', 'joint_cashflows')
                    for key in self.resources.get(kind, {})}
        if set(self.origins) != expected:
            raise StorageError('STOPPED_CAPITAL_HISTORY_PUBLICATION_MISSING')
        for key, (_, command) in self.cash_origins.items():
            if self.resources['cashflows'][key] != command:
                raise StorageError('STOPPED_CAPITAL_HISTORY_CASHFLOW_CONFLICT')
        self.timeline = sorted((revision, kind, key) for (kind, key), revision in self.origins.items())
        for revision, kind, key in self.timeline:
            if kind == 'joint_cashflows':
                operation = self.resources[kind][key]['operation_id']
                if self.operations.get('joint-done:' + operation, {}).get('revision') != revision:
                    raise StorageError('STOPPED_CAPITAL_HISTORY_EVENT_CONFLICT')
        self.money = {'fills': {}, 'cashflows': {}}
        self.cursor, self.last_revision = 0, 0
        self.cached_digest, self.cached_capital = None, None

    def _command(self, key, row, decoder, *, joint=False):
        self._count()
        payload = {'command': row['command'], 'owner': row['owner']}
        if joint:
            payload['cut'] = row['cut']
        if (key != row['command']['operation_id'] or row['state'] != 'APPLIED'
                or row['fingerprint'] != fingerprint(payload)):
            raise StorageError('STOPPED_CAPITAL_HISTORY_COMMAND_INVALID')
        return decoder(row['command'])

    def _count(self):
        self.rows += 1
        if self.rows > self.max_rows:
            raise StorageError('STOPPED_CAPITAL_HISTORY_CAPACITY_EXCEEDED')

    def _add(self, revision, kind, key):
        self._count()
        identity = kind, key
        self.origins[identity] = min(revision, self.origins.get(identity, revision))

    def _fill(self, revision, command):
        for fill in command.fills:
            expected = {'prepare_operation_id': command.prepare_operation_id, 'intent_id': command.intent_id,
                        'exchange_order_id': command.exchange_order_id, 'fill': fill.json()}
            if self.resources.get('fills', {}).get(fill.trade_id) != expected:
                raise StorageError('STOPPED_CAPITAL_HISTORY_FILL_CONFLICT')
            self._add(revision, 'fills', fill.trade_id)

    def _cash(self, revision, command):
        target = self.resources.get('cashflows', {}).get(command.bill_id)
        if target is None or any(target[k] != value for k, value in command.json().items()
                                 if k not in ('operation_id', 'observed_at')):
            raise StorageError('STOPPED_CAPITAL_HISTORY_CASHFLOW_CONFLICT')
        self._add(revision, 'cashflows', command.bill_id)
        old = self.cash_origins.get(command.bill_id)
        if old is None or revision < old[0]:
            self.cash_origins[command.bill_id] = revision, command.json()

    def before(self, revision):
        if revision <= self.last_revision:
            raise StorageError('STOPPED_CAPITAL_HISTORY_REVISION_INVALID')
        self.last_revision = revision
        changed = self.cached_capital is None
        while self.cursor < len(self.timeline) and self.timeline[self.cursor][0] < revision:
            _, kind, key = self.timeline[self.cursor]
            self.money.setdefault(kind, {})[key] = self.resources[kind][key]
            self.cursor += 1
            changed = True
        if changed:
            # Explicit offline work bound includes repeated causal replay when
            # late observations change earlier exchange-time ordering. NAVs on
            # the same committed money cut share one hash/replay, not N scans.
            self.remaining_work -= max(1, self.cursor)
            if self.remaining_work < 0:
                raise StorageError('STOPPED_CAPITAL_HISTORY_WORK_EXCEEDED')
            self.cached_digest = fingerprint(self.money)
            self.cached_capital = replay_capital(
                self.policy, self.resources.get('commands', {}), self.money['fills'], self.money['cashflows'],
                self.money.get('joint_cashflows', {}), validate_cashflow=self.validate_cashflow)
        return self.cached_digest, self.cached_capital
