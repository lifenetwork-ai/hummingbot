"""Explicit provisional-quote repository; readback never recreates acceptance.

Birth is non-monetary metadata. Preparation uses the original financial core
and publishes its action/history atomically. Neither returned DTO permits HTTP.
The configured service does not select this protocol until separately qualified.
"""
import json
from dataclasses import dataclass, field, replace

import asyncpg

from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import PrepareQuoteAction
from hummingbot.strategy_v2.life_liquidity.storage.actions import RunnerActionFacts, SpotQuoteAction
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    ResourceVersion,
    StateCut,
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent


@dataclass(frozen=True)
class QuoteBirth:
    ticket_id: str
    action: SpotQuoteAction
    born_at_ms: int
    body_json: str
    historical: bool
    preparation_cut: FinancialCut | None = field(default=None, repr=False, compare=False)
    trading_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class QuoteBirthRetirement:
    retirement_id: str
    ticket_id: str
    retired: bool
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


class PgQuoteBirthRepository:
    def __init__(self, financial):
        if type(financial) is not PgSpotRepository:
            raise StorageError('PG_BIRTH_REPOSITORY_REQUIRED')
        self.financial, self.runtime = financial, financial._runtime
        self._context = financial, financial._runtime
        self._issued, self._used_tickets, self._used_intents, self._preparing = {}, set(), set(), set()

    def _original(self):
        financial, runtime = self._context
        if self.financial is not financial or self.runtime is not runtime or financial._runtime is not runtime:
            raise StorageError('PG_BIRTH_CONTEXT_CHANGED')

    @staticmethod
    def _birth(body, historical):
        try:
            action = SpotQuoteAction(**body['action'])
            if (type(body['born_at_ms']) is not int or body['born_at_ms'] <= 0
                    or body['intent_id'] != action.intent_id):
                raise ValueError
            return QuoteBirth(body['ticket_id'], action, body['born_at_ms'],
                              json.dumps(body, sort_keys=True, separators=(',', ':')), historical)
        except (KeyError, TypeError, ValueError):
            raise StorageUnavailable('PG_BIRTH_RECEIPT_INVALID') from None

    async def issue(self, ticket_id, intent, *, model_hash, expected_version):
        self._original()
        identifier(ticket_id)
        if (len(ticket_id) > 96 or type(intent) is not PgSpotQuoteIntent
                or type(expected_version) is not int or not 0 <= expected_version <= 9223372036854775807
                or type(model_hash) is not str or len(model_hash) != 64
                or any(c not in '0123456789abcdef' for c in model_hash)):
            raise StorageError('PG_BIRTH_REQUEST_INVALID')
        if ticket_id in self._used_tickets or intent.intent_id in self._used_intents:
            raise StorageError('PG_BIRTH_IDENTITY_USED')
        self._used_tickets.add(ticket_id)
        self._used_intents.add(intent.intent_id)
        cfg = self.runtime.config
        request = dict(ticket_id=ticket_id, template=intent.capture_template().json(), model_hash=model_hash)

        def parse(value):
            if value.get('replayed') is not False or value.get('trading_permission') is not False:
                raise StorageUnavailable('PG_BIRTH_RECEIPT_INVALID')
            ticket = self._birth(value['body'], False)
            if (ticket.ticket_id != ticket_id or ticket.action.json() | {'payload_json': request['template']['payload_json']}
                    != request['template']):
                raise StorageUnavailable('PG_BIRTH_RECEIPT_INVALID')
            if any(m.version == 133 for m in self.runtime._bundle):
                try:
                    proposal = value['preparation_cut']
                    if (type(proposal) is not dict or set(proposal) != {'version', 'digest'}
                            or type(proposal['version']) is not int or proposal['version'] != expected_version):
                        raise ValueError
                    checkpoint = StateCut(cfg.binding, expected_version, expected_version,
                                          (ResourceVersion('account', expected_version),), proposal['digest'])
                    ticket = replace(ticket, preparation_cut=FinancialCut(checkpoint, (('postgres_account', proposal['digest']),)))
                except (KeyError, TypeError, ValueError, StorageError):
                    raise StorageUnavailable('PG_BIRTH_RECEIPT_INVALID') from None
            return ticket
        ticket = await self.runtime._transaction(
            'SELECT life_financial.quote_birth_issue($1,$2,$3,$4,$5)',
            (cfg.account_id, cfg.binding.json(), intent.owner.json(), expected_version, request), intent.owner, parse)
        self._issued[intent.intent_id] = ticket
        return ticket

    async def read(self, ticket_id):
        self._original()
        identifier(ticket_id)

        async def read(c):
            try:
                return await c.fetchval('SELECT life_financial.quote_birth_read($1,$2)', self.runtime.config.account_id, ticket_id)
            except asyncpg.PostgresError as error:
                raise sql_error(error) from None
        body = await self.runtime._admit(read, safety=True, operation='quote_birth_read')
        return None if body is None else self._birth(body, True)

    async def prepare(self, ticket, command, facts, *, cut, owner):
        self._original()
        if (type(ticket) is not QuoteBirth or ticket.historical
                or self._issued.get(ticket.action.intent_id) is not ticket
                or ticket.action.intent_id in self._preparing or type(command) is not PrepareQuoteAction
                or command.action != ticket.action or type(facts) is not RunnerActionFacts):
            raise StorageError('PG_BIRTH_ORIGINAL_PREPARATION_REQUIRED')
        self._preparing.add(ticket.action.intent_id)
        version, digest = self.financial._cut(cut, owner)
        cfg = self.runtime.config
        request = dict(ticket_id=ticket.ticket_id, **command.json(), dispatch=facts.json())
        identity = fingerprint(dict(kind='QUOTE_BIRTH_PREPARE', request=request, binding=cfg.binding.json(), owner=owner.json(),
                                    version=version, cut=digest))
        return await self.runtime._transaction(
            'SELECT life_financial.quote_birth_prepare($1,$2,$3,$4,$5,$6,$7)',
            (cfg.account_id, cfg.binding.json(), owner.json(), version, digest, identity, request), owner, self.financial._receipt)

    @staticmethod
    def _retirement(value):
        if (value.get('kind') != 'QUOTE_BIRTH_RETIRE' or value.get('retired') is not True
                or value.get('trading_permission') is not False or type(value.get('replayed')) is not bool):
            raise StorageUnavailable('PG_BIRTH_RETIREMENT_RECEIPT_INVALID')
        identifier(value['retirement_id'])
        identifier(value['ticket_id'])
        return QuoteBirthRetirement(value['retirement_id'], value['ticket_id'], True, value['replayed'])

    async def retire(self, retirement_id, ticket_id, *, cut, owner):
        """Permanently prohibit preparation; retain every original monetary claim.

        This observation command accepts cold origins because it grants no
        sender or release. Account serialization makes retirement/prepare
        mutually exclusive, including a still-running original executor.
        """
        self._original()
        identifier(retirement_id)
        identifier(ticket_id)
        if len(retirement_id) > 96 or len(ticket_id) > 96:
            raise StorageError('PG_BIRTH_RETIREMENT_REQUEST_INVALID')
        version, digest = self.financial._cut(cut, owner)
        cfg = self.runtime.config
        command = dict(retirement_id=retirement_id, ticket_id=ticket_id, reason='UNCONSUMED_BIRTH_RETIRED')
        identity = fingerprint(dict(kind='QUOTE_BIRTH_RETIRE', binding=cfg.binding.json(), owner=owner.json(),
                                    version=version, cut=digest, command=command))
        return await self.runtime._transaction(
            'SELECT life_financial.quote_birth_retire($1,$2,$3,$4,$5,$6,$7)',
            (cfg.account_id, cfg.binding.json(), owner.json(), version, digest, identity, command),
            owner, self._retirement, observation=True)

    async def lookup_retirement(self, retirement_id):
        self._original()
        identifier(retirement_id)

        async def read(c):
            try:
                return await c.fetchval('SELECT life_financial.quote_birth_retire_read($1,$2)',
                                        self.runtime.config.account_id, retirement_id)
            except asyncpg.PostgresError as error:
                raise sql_error(error) from None
        value = await self.runtime._admit(read, safety=True, operation='quote_birth_retire_read')
        return None if value is None else self._retirement(value)
