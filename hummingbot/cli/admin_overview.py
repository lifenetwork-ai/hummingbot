"""Read-only Overview projections. Missing accounting sources remain unknown."""
import math
from datetime import datetime
from decimal import Decimal

from pydantic import ValidationError

from hummingbot.cli.admin_overview_models import (
    OverviewAccountScope,
    OverviewCapitalAllocated,
    OverviewDepth,
    OverviewHoldBenchmark,
    OverviewInventoryLimit,
    OverviewMarketData,
    OverviewObservation,
    OverviewPortfolio,
    OverviewPostFillRecovery,
    OverviewReferencePrice,
    OverviewReservedBalance,
    OverviewRiskRule,
    OverviewRollingCoverage,
    OverviewSession,
    OverviewSessionDelta,
    OverviewSessionFees,
    OverviewSlippage,
    OverviewSupplement,
)
from hummingbot.core.reporting import reporting_view


def unavailable(reason):
    return dict(value=None, observed_at=None, stale=True, unavailable_reason=reason)


def _number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Non-finite accounting value')
    return result


def _epoch(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Invalid observation time')
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError('Naive observation time')
        return value.timestamp()
    return float(value)


def _fresh(stamp, now, maximum=5):
    try:
        return math.isfinite(_epoch(stamp)) and 0 <= now - _epoch(stamp) <= maximum
    except (TypeError, ValueError, OverflowError):
        return False


def _observation(model, raw, *, now, reason=None):
    if reason:
        return OverviewObservation[model](**unavailable(reason))
    try:
        value = OverviewObservation[model].model_validate(raw)
        if value.value is not None and (value.stale or not _fresh(value.observed_at.timestamp() if value.observed_at else None, now)):
            return OverviewObservation[model](**unavailable('observation_stale'))
        return value
    except (TypeError, ValueError, ValidationError, AttributeError):
        return OverviewObservation[model](**unavailable('observation_invalid'))


def _account_scope(reporting):
    bindings = reporting.get('active_account_bindings')
    if not isinstance(bindings, dict) or not bindings:
        return OverviewAccountScope(account_ids=[], environment=None, identity_verified=False), []
    identities = set()
    for connector, binding in bindings.items():
        if connector not in ('okx', 'okx_perpetual') or not isinstance(binding, dict):
            continue
        uid, environment = binding.get('account_id'), binding.get('environment')
        if not isinstance(uid, str) or not uid or environment not in ('live', 'demo'):
            return OverviewAccountScope(account_ids=[], environment=None, identity_verified=False), []
        identities.add((uid, environment))
    # Both connectors share one account in managed MM. Never sum across a
    # credential rotation or silently mix a live and demo account.
    if len(identities) != 1:
        return OverviewAccountScope(account_ids=[], environment=None, identity_verified=False), []
    uid, environment = identities.pop()
    accounts = [item for item in reporting.get('accounts', [])
                if item.get('account_id') == uid and item.get('environment') == environment]
    verified = len(accounts) == 1 and accounts[0].get('identity_verified') is True
    return OverviewAccountScope(account_ids=[uid], environment=environment, identity_verified=verified), accounts


def _portfolio(accounts, prices, *, now, reason):
    if reason:
        return unavailable(reason)
    try:
        balances = accounts[0]['balances']
        if not balances or any(item.get('stale') or not item.get('present_in_latest_snapshot', True) for item in balances):
            return unavailable('balance_observation_unavailable')
        amounts = {item['currency']: _number(item['equity']) for item in balances}
        if len(amounts) != len(balances) or 'USDT' not in amounts:
            return unavailable('balance_observation_unavailable')
        other = {asset: amount for asset, amount in amounts.items() if asset not in ('USDT', 'LIFE') and amount != 0}
        if other:
            return unavailable('asset_valuation_unavailable')
        if not isinstance(prices, dict) or prices.get('stale') or not _fresh(prices.get('observed_at'), now):
            return unavailable('valuation_price_unavailable')
        life = amounts.get('LIFE', Decimal('0'))
        price_rows = prices.get('value')
        if not isinstance(price_rows, list):
            return unavailable('valuation_price_unavailable')
        candidates = [item for item in price_rows if item.get('asset') == 'LIFE' and item.get('connector') == 'okx'
                      and item.get('pair') == 'LIFE-USDT' and _fresh(item.get('observed_at', prices.get('observed_at')), now)]
        if life != 0 and len(candidates) != 1:
            return unavailable('valuation_price_unavailable')
        price = _number(candidates[0]['price_usdt']) if candidates else Decimal('1')
        if price <= 0:
            return unavailable('valuation_price_unavailable')
        # USDT equity already includes the venue's perpetual unrealized PnL.
        # Position PnL and collateral must never be added a second time.
        life_usdt = life * price
        valuation_prices = [{key: item[key] for key in ('asset', 'pair', 'connector', 'price_usdt', 'observed_at')} for item in candidates]
        price_stamp = min((_epoch(item['observed_at']) for item in candidates), default=_epoch(prices['observed_at']))
        stamp = min(price_stamp, *(_epoch(item['observed_at']) for item in balances))
        return dict(value=dict(total_usdt=amounts['USDT'] + life_usdt, usdt=amounts['USDT'], life_usdt=life_usdt,
                               other_assets_usdt='0', valuation=dict(currency='USDT', valuation_mark='mid',
                                                                     valued_at=price_stamp, prices=valuation_prices)),
                    observed_at=stamp, stale=False, unavailable_reason=None)
    except (KeyError, ValueError, TypeError, ArithmeticError, IndexError):
        return unavailable('balance_observation_unavailable')


def _balances(accounts, *, now, reason):
    if reason:
        return unavailable(reason)
    try:
        balances = accounts[0]['balances']
        if not balances or any(item.get('stale') or not item.get('present_in_latest_snapshot', True) for item in balances):
            return unavailable('balance_observation_unavailable')
        values = [dict(asset=item['currency'], free=_number(item['available_cash']) if item.get('available_cash') is not None else None,
                       reserved_for_open_orders=None) for item in sorted(balances, key=lambda item: item['currency'])]
        return dict(value=values, observed_at=min(float(item['observed_at']) for item in balances),
                    stale=False, unavailable_reason='open_order_reservation_unavailable')
    except (KeyError, ValueError, TypeError, ArithmeticError, IndexError):
        return unavailable('balance_observation_unavailable')


def _risk_rules(snapshot, *, now, live, runtime_live=False, session=None):
    rules, stamps = [], []
    policy_complete = True
    drawdown = snapshot.get('mm_drawdown')
    if isinstance(drawdown, dict):
        fresh = live and drawdown.get('ready') and not drawdown.get('stale') and _fresh(drawdown.get('observed_at'), now)
        pairs = [('drawdown_global', None, drawdown.get('max_global_drawdown_quote'), drawdown.get('global_drawdown_quote'))]
        pairs.extend(('drawdown_controller', identity, drawdown.get('max_controller_drawdown_quote'), item.get('drawdown_quote'))
                     for identity, item in (drawdown.get('controllers') or {}).items())
        for identity, controller, limit, current in pairs:
            if limit is None:
                continue
            try:
                limit = _number(limit)
                current = _number(current) if fresh and current is not None else None
                # This is the existing policy comparison, never enforcement.
                status = ('disabled' if not drawdown.get('enabled') else 'unknown' if current is None
                          else 'violated' if current > limit else 'ok')
                latched = (live and not drawdown.get('stale') and _fresh(drawdown.get('observed_at'), now)
                           and ((identity == 'drawdown_global' and drawdown.get('reason_code') == 'drawdown_global_limit')
                                or (controller is not None and drawdown.get('breached_controller_id') == controller)))
                if latched:
                    status = 'violated'
                rules.append(dict(rule_id=identity, controller_id=controller, market=None, unit='USDT',
                                  lower_limit=None, upper_limit=limit, current=current, status=status))
            except (ValueError, ArithmeticError):
                continue
        if fresh:
            stamps.append(_epoch(drawdown['observed_at']))
    outflow = snapshot.get('spot_outflow')
    if isinstance(outflow, dict):
        try:
            limit = _number(outflow['policy']['max_net_outflow_quote'])
            fresh = live and outflow.get('ready') and not outflow.get('stale') and _fresh(outflow.get('observed_at'), now)
            # Existing risk budget consumes recorded net outflow AND live reservations.
            current = (max(Decimal('0'), _number(outflow['recorded_net_outflow_quote'])) + _number(outflow['reserved_quote'])) if fresh else None
            rules.append(dict(rule_id='spot_outflow', controller_id=None, market='spot', unit='USDT', lower_limit=None,
                              upper_limit=limit, current=current,
                              status='unknown' if current is None else 'violated' if current >= limit else 'ok'))
            if fresh:
                stamps.append(_epoch(outflow['observed_at']))
        except (KeyError, ValueError, TypeError, ArithmeticError):
            pass
    policies = ((session or {}).get('catalogue') or {}).get('markets') or {}
    selection = ((session or {}).get('request') or {}).get('market_mode')
    observed_markets = {item['market']: item for item in snapshot.get('mm_markets') or [] if isinstance(item, dict) and 'market' in item}
    for market, config in policies.items():
        if selection not in (market, 'both'):
            continue
        guard = config.get('quote_guards') or {}
        if not guard.get('enabled'):
            continue
        try:
            limit = _number(guard['max_deviation_bps'])
            observed = observed_markets.get(market, {}).get('quote_guard') or {}
            fresh = live and _fresh(observed.get('evaluated_at'), now)
            current = _number(observed['deviation_bps']) if fresh and observed.get('deviation_bps') is not None else None
            status = ('unknown' if current is None else 'violated' if current > limit
                      else 'ok' if observed.get('ready') else 'unknown')
            rules.append(dict(rule_id='quote_deviation', controller_id=config.get('id'), market=market, unit='bps',
                              lower_limit=None, upper_limit=limit, current=current, status=status))
            if fresh:
                stamps.append(_epoch(observed['evaluated_at']))
            if guard.get('mode') == 'bootstrap_then_market':
                # Detailed bootstrap admission is already in /status; the flat
                # summary does not claim to cover its multi-stage conditions.
                policy_complete = False
        except (KeyError, ValueError, TypeError, ArithmeticError):
            policy_complete = False
    runtime = snapshot.get('mm_overview')
    runtime = runtime if isinstance(runtime, dict) else {}
    limits = runtime.get('inventory_limits')
    limits = limits if isinstance(limits, dict) else {}
    inventory_observed = runtime_live and not limits.get('stale') and _fresh(limits.get('observed_at'), now) and isinstance(limits.get('value'), list) and bool(limits['value'])
    complete = policy_complete and inventory_observed
    if inventory_observed:
        items = limits.get('value')
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                complete = False
                continue
            required = ('inventory_limit',) if item.get('market') == 'spot' else ('long_limit', 'short_limit', 'gross_limit')
            if any(item.get(field) is None for field in required):
                complete = False
            for field in ('inventory_limit', 'long_limit', 'short_limit', 'gross_limit'):
                try:
                    if item.get(field) is None:
                        continue
                    limit = _number(item[field])
                    rules.append(dict(rule_id='inventory_base' if field == 'inventory_limit' else 'inventory_' + field.removesuffix('_limit'), controller_id=item['controller_id'],
                                      market=item['market'], unit='LIFE', lower_limit=None, upper_limit=limit,
                                      current=None, status='unknown'))
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    complete = False
                    continue
        stamps.append(_epoch(limits['observed_at']))
    return dict(value=rules or None, observed_at=min(stamps) if stamps else None,
                stale=not live, unavailable_reason='risk_policy_unavailable' if not rules else None if complete else 'risk_policy_observation_incomplete')


def build_overview(row, *, fees=None):
    """Project the exact row read by /status; no database/network/control work."""
    try:
        now = float(row['database_now'])
        if not math.isfinite(now):
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    snapshot = row.get('status') or {}
    session = row.get('mm_session') or {}
    observed_session = snapshot.get('mm_session') or {}
    owner = bool(row.get('owner_uuid')) and _fresh(now - float(row['heartbeat_age_s']), now) if row.get('heartbeat_age_s') is not None else False
    matches = (session.get('id'), session.get('generation')) == (observed_session.get('id'), observed_session.get('generation'))
    reason = 'owner_observation_unavailable' if not owner else 'session_observation_mismatch' if not matches else None
    runtime = snapshot.get('mm_overview')
    runtime = runtime if isinstance(runtime, dict) else {}
    runtime_matches = ((runtime.get('session_id'), runtime.get('generation')) == (session.get('id'), session.get('generation')))
    runtime_reason = reason or ('session_observation_mismatch' if not runtime_matches else None)
    if not runtime_reason and not _fresh(runtime.get('observed_at'), now):
        runtime_reason = 'owner_observation_unavailable'
    try:
        view = reporting_view(row)
        reporting = {**(snapshot.get('reporting') or {}), 'accounts': view['accounts']}
        scope, accounts = _account_scope(reporting)
    except (KeyError, ValueError, TypeError, ArithmeticError):
        scope, accounts = OverviewAccountScope(account_ids=[], environment=None, identity_verified=False), []
    account_reason = reason or (None if scope.identity_verified else 'account_identity_unavailable')
    portfolio = _portfolio(accounts, runtime.get('valuation_prices'), now=now, reason=account_reason or runtime_reason)
    markets = []
    models = dict(depth=OverviewDepth, coverage=OverviewRollingCoverage,
                  slippage_estimate=OverviewSlippage, post_fill_recovery=OverviewPostFillRecovery)
    runtime_markets = runtime.get('markets')
    for item in runtime_markets[:2] if isinstance(runtime_markets, list) else []:
        if not isinstance(item, dict) or item.get('market') not in ('spot', 'perpetual') or not isinstance(item.get('pair'), str):
            continue
        try:
            markets.append(OverviewMarketData(market=item['market'], connector=item['connector'], pair=item['pair'],
                                              **{key: _observation(model, item.get(key), now=now, reason=runtime_reason) for key, model in models.items()}))
        except (ValidationError, KeyError, TypeError):
            continue
    return OverviewSupplement(
        contract_version=1, server_time=now, account_scope=scope,
        session_id=session.get('id'), session_started_at=session.get('started_at'),
        session=OverviewSession(
            capital_allocated=OverviewObservation[OverviewCapitalAllocated](**unavailable('capital_mandate_not_configured')),
            fees=OverviewObservation[OverviewSessionFees].model_validate(fees or unavailable('session_fees_unavailable')),
            delta=OverviewObservation[OverviewSessionDelta](**unavailable('session_baseline_and_transfers_unavailable')),
            benchmark_hold=OverviewObservation[OverviewHoldBenchmark](**unavailable('initial_holdings_and_transfers_unavailable')),
        ),
        portfolio=OverviewObservation[OverviewPortfolio].model_validate(portfolio),
        balances=OverviewObservation[list[OverviewReservedBalance]].model_validate(_balances(accounts, now=now, reason=account_reason)),
        risk_rules=OverviewObservation[list[OverviewRiskRule]].model_validate(
            _risk_rules(snapshot, now=now, live=owner and matches, runtime_live=runtime_reason is None, session=session)),
        markets=markets,
        inventory_limits=_observation(list[OverviewInventoryLimit], runtime.get('inventory_limits'), now=now, reason=runtime_reason),
        references=_observation(list[OverviewReferencePrice], runtime.get('references'), now=now, reason=runtime_reason),
    )
