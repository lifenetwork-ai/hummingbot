"""Shared SPOT claim hooks inside the original durable preparation saga.

Recovery restores a hold from an admitted original request; it grants no send
permission. Existing native joint source proof exclusively releases the claim.
"""
from dataclasses import asdict

from hummingbot.strategy_v2.life_liquidity.send_gate import SendPermit
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim, _json
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint, thaw
from hummingbot.strategy_v2.life_liquidity.storage.financial import PrepareSpot


class SharedSpotPreparation:
    def __init__(self, joint):
        self.joint, self.store = joint, joint.store
        self.capital = joint._recovery.capital

    def _policy(self):
        return {'joint': self.joint._policy(), 'capital': self.capital.journal.policy}

    def _guard(self):
        self.store.assert_worker_thread()
        meta = self.store._read()
        if (self.store._shared_spot is not self or self.joint.store is not self.store
                or self.capital is not self.joint._recovery.capital
                or thaw(meta.resources.get('shared_spot_policy')) != self._policy()):
            raise StorageError('SHARED_SPOT_BINDING_MISMATCH')
        return meta

    def validate(self, command):
        self._guard()
        if type(command) is not PrepareSpot or command.risk_epoch is None:
            raise StorageError('SHARED_SPOT_RISK_PROVENANCE_REQUIRED')

    @staticmethod
    def permit(command):
        return SendPermit(command.intent_id, command.client_order_id, command.intent_id,
                          command.session_id, command.epoch, command.config_version,
                          command.risk_epoch, command.limit_price_usdt, command.quantity_base)

    def claim(self, command):
        self.validate(command)
        return CapitalClaim.spot(self.permit(command), command.side, self.store.binding.account_uid)

    def preflight(self, command):
        claim = self.claim(command)
        result = self.capital.check()
        if not result.allowed:
            raise StorageError(result.reason_code)
        observed = self.capital.qualified_snapshot()
        if (observed.spot_life_base, observed.spot_cash_quote) != (
                self.store._ledger.life_balance, self.store._ledger.usdt_balance):
            raise StorageError('SHARED_SPOT_ACCOUNT_MISMATCH')
        result = self.capital._evaluate(observed, {**self.capital.claims(), claim.intent_id: claim})
        if not result.allowed:
            raise StorageError(result.reason_code)

    def reserve(self, command):
        self.validate(command)
        result = self.capital.spot(self.permit(command), command.side,
                                   (self.store._ledger.life_balance, self.store._ledger.usdt_balance), reserve=True)
        if not result.allowed:
            raise StorageError(result.reason_code)
        self.verify_hold(command)

    def verify_hold(self, command):
        claim = self.claim(command)
        if self.capital.claims().get(claim.intent_id) != claim:
            raise StorageError('SHARED_SPOT_ORIGINAL_CLAIM_REQUIRED')

    def recover_hold(self, command, facts, owner):
        # The caller has already completed the original preparation recovery
        # guard and actual native-subset/final account evidence checks. Repeat
        # immutable provenance here before any shared journal publication.
        meta = self._guard()
        record = meta.resources['commands'].get(command.operation_id)
        if (record is None or record['state'] != 'PREPARING' or record['command'] != command.json()
                or record.get('original_owner') != owner.json()
                or record['fingerprint'] != fingerprint({'command': command.json(),
                                                         'cut': record['original_cut'], 'owner': owner.json()})
                or facts.binding != self.store.binding or meta.ownership.token != owner):
            raise StorageError('SHARED_SPOT_RECOVERY_ORIGIN_REQUIRED')
        # No fresh risk allocation or native rearm: preserve a possibly
        # committed original hold even if current limits would reject a new
        # order. Further sends still require a distinct qualified activation.
        claim = self.claim(command)
        if self.store._wal.has_intent(claim.intent_id):
            wal = self.store._wal.get(claim.intent_id)
            if wal.state != 'PREPARED' or wal.exchange_order_id is not None:
                raise StorageError('SHARED_SPOT_RECOVERY_ATTEMPT_CHANGED')
        observed = self.capital.qualified_snapshot()
        if (observed.spot_life_base, observed.spot_cash_quote) != (
                facts.account_base_life, facts.account_quote_usdt):
            raise StorageError('SHARED_SPOT_RECOVERY_ACCOUNT_MISMATCH')
        with self.capital.journal.locked() as state:
            claims = self.capital._decode(state)
            self.capital._validate_claim(claim)
            if (claim.intent_id in state['settled']
                    or any(v.wire_id == claim.wire_id and v != claim for v in claims.values())
                    or claim.intent_id in claims and claims[claim.intent_id] != claim):
                raise StorageError('SHARED_SPOT_RECOVERY_CLAIM_CHANGED')
            if claim.intent_id not in claims:
                state['claims'][claim.intent_id] = _json(asdict(claim))
                self.capital.journal.commit(state)
                self.capital._retained = self.capital._decode(state)
        self.verify_hold(command)


async def bind_shared_spot(joint):
    from hummingbot.strategy_v2.life_liquidity.storage.joint import FileJointRepository
    if type(joint) is not FileJointRepository or not joint._couple_spot:
        raise StorageError('SHARED_SPOT_COUPLED_REPOSITORY_REQUIRED')
    store = joint.store
    context = SharedSpotPreparation(joint)

    def install():
        store.assert_worker_thread()
        if store._shared_spot is not None:
            raise StorageError('SHARED_SPOT_ALREADY_BOUND')
        meta = store._read()
        prior = meta.resources.get('shared_spot_policy')
        if prior is None:
            if (not store._created_offline or meta.resources['commands'] or context.capital.claim_ids()):
                raise StorageError('SHARED_SPOT_IMPORT_REQUIRED')
            store._commit(meta.cut, joint.owner, 'shared-spot:bind',
                          (('shared_spot_policy', encode(context._policy())),))
        elif thaw(prior) != context._policy():
            raise StorageError('SHARED_SPOT_BINDING_MISMATCH')
        store._shared_spot = context
        context._guard()
    await store._call(install)
    return context
