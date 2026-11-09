"""Admin-only review/confirm recovery handlers on the existing API worker."""
from typing import Any, Dict, List, Literal, Optional

from aiohttp import web
from pydantic import Field

from hummingbot.cli.admin_errors import request_id
from hummingbot.cli.admin_models import APIModel, UTCDateTime
from hummingbot.cli.api_documentation import api_operation
from hummingbot.core.recovery import recovery_blockers
from hummingbot.core.recovery_messages import RECOVERY_MESSAGES
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.recovery_store import RecoveryStore

HARD_BLOCKERS = frozenset({'recovery_stop_required', 'recovery_stop_incomplete', 'recovery_not_halted',
                           'credential_operation_in_progress', 'recovery_pending_mutations',
                           'owner_unavailable', 'recovery_operation_in_progress'})


class RecoveryCheckRequest(APIModel):
    expected_version: int = Field(ge=0, strict=True)


class RecoveryResetRequest(RecoveryCheckRequest):
    check_id: str = Field(min_length=1, max_length=128, strict=True)
    confirmation_token: str = Field(pattern=r'^[a-f0-9]{32}$', strict=True)


class RecoveryBlocker(APIModel):
    code: str
    detail: str
    next_action: str


class RecoveryPositionValues(APIModel):
    side: Literal['BUY', 'SELL']
    amount: str
    volume_traded_quote: str
    breakeven_price: str
    realized_pnl_quote: str
    unrealized_pnl_quote: str
    cum_fees_quote: str


class RecoveryPositionRepair(APIModel):
    id: str
    controller_id: str
    connector: Literal['okx', 'okx_perpetual']
    pair: str
    before: RecoveryPositionValues
    after: RecoveryPositionValues
    valuation_basis: Literal['reconstructed_average_entry_not_live_pnl']


class RecoveryOperation(APIModel):
    id: str
    action: Literal['recovery_check', 'recovery_reset']
    status: Literal['accepted', 'applying', 'succeeded', 'failed']
    stage: str
    label: str
    detail: str
    can_recover: bool
    blockers: List[RecoveryBlocker] = Field(default_factory=list)
    proposed_changes: List[RecoveryPositionRepair] = Field(default_factory=list)
    repaired_positions: List[RecoveryPositionRepair] = Field(default_factory=list)
    confirmation_token: Optional[str] = None
    expires_at: Optional[UTCDateTime] = None
    verification: Dict[str, Any] = Field(default_factory=dict)
    trading_state: Literal['stopped'] = 'stopped'
    created_at: UTCDateTime
    updated_at: UTCDateTime


class RecoveryOverview(APIModel):
    halted: bool
    control_version: int
    check_available: bool
    reset_available: bool
    blockers: List[RecoveryBlocker]
    operation: Optional[RecoveryOperation] = None


def blocker(code):
    action, detail = RECOVERY_MESSAGES.get(code, ('contact_support', 'Technical review is required before recovery.'))
    return dict(code=code, detail=detail, next_action=action)


def operation_response(row):
    report = row.get('result') or {}
    stage = report.get('stage', 'queued')
    labels = {'queued': ('Recovery queued', 'Wait for the trading service to check this request.'),
              'checking_exchange': ('Checking recovery', 'Checking saved history against the exchange. Trading remains stopped.'),
              'revalidating': ('Recovering', 'Rechecking the reviewed plan before applying verified repairs.'),
              'review_required': ('Ready for review', 'Review the proposed repairs, then confirm recovery. Trading will stay stopped.'),
              'blocked': ('Recovery blocked', 'Resolve the listed reasons before running a new recovery check.'),
              'recovered': ('Recovery complete', 'Trading remains stopped. Start a new session separately when ready.')}
    label, detail = labels.get(stage, labels['blocked'])
    return RecoveryOperation(id=row['id'], action=row['action'], status=row['status'], stage=stage,
                             label=label, detail=detail, can_recover=bool(report.get('can_recover')),
                             blockers=[blocker(code) for code in report.get('blockers', [])],
                             proposed_changes=report.get('proposed_changes', []), repaired_positions=report.get('repaired_positions', []),
                             confirmation_token=report.get('confirmation_token'), expires_at=report.get('expires_at'),
                             verification=report.get('verification', {}), created_at=row['created_at'], updated_at=row['updated_at'])


class AdminRecoveryAPI:
    def recovery_store(self):
        if self.host is None or self.host.store is None:
            raise ControlConflict('owner_unavailable', code='owner_unavailable')
        return RecoveryStore(self.host.store)

    @api_operation(operation_id='getRecovery', summary='Read recovery blockers and the latest reviewed operation',
                   tag='Recovery', response_model=RecoveryOverview)
    async def recovery(self, request):
        store = self.recovery_store()
        state = await self.run_database(store.state.read)
        if state is None:
            raise ControlConflict('owner_unavailable', code='owner_unavailable')
        codes = recovery_blockers(state)
        age = state.get('heartbeat_age_s')
        if not state.get('owner_uuid') or age is None or not 0 <= float(age) <= 5:
            codes.append('owner_unavailable')
        if (state['recovery_state'] or {}).get('recovery_operation'):
            codes.append('recovery_operation_in_progress')
        row = await self.run_database(store.latest)
        operation = operation_response(row) if row else None
        reset = bool(operation and operation.can_recover and operation.expires_at is not None
                     and operation.expires_at.timestamp() > float(state['database_now'])
                     and row['expected_version'] == state['control_version']
                     and store._same_owner(state, row['result']['_private']) and not codes)
        model = RecoveryOverview(halted=bool((state['recovery_state'] or {}).get('halt')),
                                 control_version=state['control_version'], check_available=not any(code in HARD_BLOCKERS for code in codes), reset_available=reset,
                                 blockers=[blocker(code) for code in sorted(set(codes))], operation=operation)
        return web.json_response(model.model_dump(mode='json'))

    @api_operation(operation_id='checkRecovery', summary='Check stopped history and preview verified repairs; does not clear HALT',
                   tag='Recovery', request_model=RecoveryCheckRequest, response_model=RecoveryOperation,
                   status=202, headers=('Idempotency-Key',))
    async def recovery_check(self, request):
        return await self._recovery_request(request, RecoveryCheckRequest, 'recovery_check')

    @api_operation(operation_id='resetRecovery', summary='Revalidate and apply the reviewed technical recovery; leaves trading stopped',
                   tag='Recovery', request_model=RecoveryResetRequest, response_model=RecoveryOperation,
                   status=202, headers=('Idempotency-Key',))
    async def recovery_reset(self, request):
        return await self._recovery_request(request, RecoveryResetRequest, 'recovery_reset')

    async def _recovery_request(self, request, model, action):
        payload = await self.access_body(request, model)
        key = request.headers.get('Idempotency-Key', '')
        if not 1 <= len(key) <= 128 or any(not 33 <= ord(character) <= 126 for character in key):
            raise ValueError('Idempotency-Key is required')
        row = await self.run_control_database(self.recovery_store().request, action,
                                              key, payload.model_dump(), request['admin_principal'], request_id(request))
        return web.json_response(operation_response(row).model_dump(mode='json'), status=202)

    @api_operation(operation_id='getRecoveryOperation', summary='Read exact recovery progress or outcome without contacting the exchange',
                   tag='Recovery', response_model=RecoveryOperation, path_parameters=('operation_id',))
    async def recovery_operation(self, request):
        identity = request.match_info['operation_id']
        if not 1 <= len(identity) <= 128:
            raise ValueError('Invalid recovery operation identity')
        row = await self.run_database(self.recovery_store().get, identity)
        if row is None:
            raise web.HTTPNotFound()
        return web.json_response(operation_response(row).model_dump(mode='json'))
