"""Read-only operator presentation over existing owner/control observations."""
import time
from copy import deepcopy

from hummingbot.connector.okx_environment import OKXEnvironment
from hummingbot.core.mm_liquidity import liquidity_view


def process_identity(host):
    try:
        environment = OKXEnvironment.load().mode
    except ValueError:
        environment = "unknown"
    return dict(trading_environment=environment, revision=getattr(host, "revision", None))


def operator_view(row, process):
    snapshot = row.get("status") or {}
    age = row.get("heartbeat_age_s")
    available = bool(row.get("owner_uuid")) and age is not None and 0 <= float(age) <= 5
    session, observed = row.get("mm_session") or {}, snapshot.get("mm_session") or {}
    matches = (session.get("id"), session.get("generation")) == (observed.get("id"), observed.get("generation"))
    observed_at = (snapshot.get("availability") or {}).get("observed_at")
    now = row.get("database_now", time.time())
    observation_old = observed_at is not None and not 0 <= float(now) - float(observed_at) <= 5
    stale = not available or not matches or observation_old
    recovery = row.get("recovery_summary") or {}
    halted = bool(recovery.get("halted")) or snapshot.get("phase") == "halted"
    pending = bool(recovery.get("pending_mutations"))
    owner = snapshot.get("identity")
    mismatch = bool(available and owner and owner.get("trading_environment") != process["trading_environment"])
    common = ("recovery_required" if halted or pending else "owner_unavailable" if not available else
              "environment_mismatch" if mismatch else None)
    current_active = session.get("status") in ("preparing", "running", "stopping")
    reasons = {
        "start": common or ("session_active" if row["desired_state"] == "running" or current_active else None),
        "switch": common or (None if session.get("status") in ("preparing", "running") else "session_not_active"),
        # Stop is a durable intent and remains useful during startup/owner loss.
        "stop": None if session.get("id") else "session_not_active",
    }
    from hummingbot.cli.admin_errors import MESSAGES
    descriptions = {"start": "Start a new session.", "switch": "Replace the current session.",
                    "stop": "Stop placing orders. Existing positions remain open."}
    actions = {}
    for key, reason in reasons.items():
        detail = MESSAGES.get(reason, "The action is currently unavailable.") if reason else descriptions[key]
        actions[key] = dict(allowed=reason is None, reason_code=reason, detail=detail)
    phase = snapshot.get("phase")
    if halted or pending:
        state, label, detail, next_action = "needs_attention", "Technical review needed", "Trading cannot restart until the cause is resolved.", "contact_support"
    elif mismatch:
        state, label, detail, next_action = "needs_attention", "Deployment review needed", "The responding service and trading owner use different environments.", "contact_support"
    elif not available or observation_old:
        state, label, detail, next_action = "unavailable", "Status unavailable", "The latest trading state cannot be confirmed.", "wait"
    elif row["desired_state"] == "stopped":
        stopping = bool(snapshot.get("running")) or session.get("status") == "stopping"
        state, label = ("stopping", "Stopping orders") if stopping else ("stopped", "Stopped")
        detail, next_action = "Positions may remain open. Check the exposure panel.", "wait" if stopping else "start"
    elif not matches:
        state, label, detail, next_action = "switching", "Applying session", "Waiting for the requested session to become active.", "wait"
    elif phase == "running":
        state, label, detail, next_action = "running", "Session active", "Check the liquidity panels for current orders on each market.", "monitor"
    elif phase == "waiting_for_mm_readiness":
        state, label, detail, next_action = "waiting", "Waiting for market readiness", "The service is available. Trading is waiting for required data or limits.", "wait"
    else:
        state, label, detail, next_action = "preparing", "Preparing session", "Connecting and checking the selected markets.", "wait"
    markets = deepcopy(snapshot.get("mm_markets") or [])
    quotes = {market: value['quote_activity'] for market, value in liquidity_view(row).get('markets', {}).items()
              if value.get('quote_activity')}
    if state == 'running':
        missing = [value for value in quotes.values() if value['state'] == 'no_quotes']
        if missing:
            state, next_action = 'waiting', 'monitor'
            label = 'No working orders observed' if len(missing) == len(quotes) else 'Some markets have no working orders'
            detail = 'Check the market panels for BUY and SELL reasons.'
            if any(value['warning_active'] for value in missing):
                detail = 'Working orders have been absent beyond the warning interval. Check the market panels and contact support if unresolved.'
        elif quotes and all(value['state'] == 'orders_working' for value in quotes.values()):
            label, detail = 'Orders working', 'Working orders are observed. Fill activity is shown separately for each market.'
        elif any(value['state'] == 'one_sided' for value in quotes.values()):
            label, detail = 'Orders working on one side', 'Check the side without working orders in each market panel.'
    drawdown = deepcopy(snapshot.get("mm_drawdown"))
    if not stale and state in ('running', 'waiting'):
        defenses = [(market.get('spread_policy') or {}).get('defense') for market in markets]
        defenses = [value for value in defenses if value]
        restricted = next((value for value in defenses if value.get('state') in ('paused', 'waiting', 'checkpoint_pending')), None)
        reduced = next((value for value in defenses if value.get('state') in ('reduced', 'static_fallback')), None)
        if restricted:
            state, label, detail, next_action = 'waiting', restricted['label'], restricted['next_action'], 'wait'
        elif reduced and state == 'running':
            label, detail = reduced['label'], reduced['next_action']
    if halted or pending:
        for market in markets:
            defense = (market.get('spread_policy') or {}).get('defense')
            if defense:
                defense.update(automatic_recovery_expected=False, next_action='Contact support before restarting trading.')
    if stale:
        for market in markets:
            market.update(ready=False, admitted=False, reference_price=None)
            if market.get("quote_guard"):
                market["quote_guard"].update(ready=False, can_admit=False)
            if market.get('spread_policy'):
                market['spread_policy'].update(stale=True, raw_natr=None, multiplier=None, buy_spreads=[], sell_spreads=[])
                if market['spread_policy'].get('defense'):
                    market['spread_policy']['defense'].update(state='unavailable', reason_code='observation_stale',
                                                              label='Volatility status unavailable', next_action='Wait for current service observations.',
                                                              automatic_recovery_expected=False, size_factor='0', effective_allocation_quote='0',
                                                              cancellation_pending=None, paused_latch_persisted=None)
        if drawdown:
            drawdown.update(ready=False, stale=True, global_pnl_quote=None, global_drawdown_quote=None)
            for value in drawdown.get("controllers", {}).values():
                value.update(pnl_quote=None, drawdown_quote=None)
    return dict(process_identity=process, owner_identity=owner, quote_activity=quotes,
                observation=dict(stale=stale, session_matches=matches, observed_at=observed_at,
                                 reason_code="owner_unavailable" if not available else "session_transition" if not matches else
                                 "observation_stale" if observation_old else None),
                operator=dict(state=state, label=label, detail=detail, next_action=next_action), actions=actions,
                recovery=dict(halted=halted, pending_mutations=pending, next_action="contact_support" if halted or pending else "none",
                              detail="Contact support before restarting trading." if halted or pending else "No recovery block is recorded."),
                session_configuration_url=f"/api/v1/admin/sessions/{session['id']}/configuration" if session.get("id") else None,
                markets=markets, drawdown=drawdown)
