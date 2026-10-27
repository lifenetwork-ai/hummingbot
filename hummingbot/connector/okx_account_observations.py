"""Retain OKX account fields already fetched by the connector's normal loop.

Engine balances may represent equity. Keep exchange cash fields for reporting
without changing the connector's trading balance semantics or valuing positions.
"""
import time
from decimal import Decimal, InvalidOperation


def account_identity(connectors):
    """Read the already-observed common OKX account; never make another request."""
    identities = {(getattr(connector, 'account_uid', None), getattr(connector, 'trading_environment', None))
                  for name, connector in connectors.items() if name in ('okx', 'okx_perpetual')}
    if not identities or any(not isinstance(uid, str) or not uid or environment not in ('live', 'demo')
                             for uid, environment in identities):
        raise ValueError('identity_unavailable')
    if len(identities) != 1:
        raise ValueError('binding_mismatch')
    uid, environment = identities.pop()
    return dict(account_id=uid, environment=environment)


async def observe_account_identity(connector):
    """One authenticated read per connector, retried conservatively on failure."""
    if getattr(connector, "account_uid", None) or time.time() < getattr(connector, "_account_identity_retry_at", 0):
        return
    connector._account_identity_retry_at = time.time() + 60
    try:
        response = await connector._api_get(path_url="/api/v5/account/config", is_auth_required=True)
        if str(response.get("code")) == "0" and response.get("data") and response["data"][0].get("uid"):
            connector.account_uid = str(response["data"][0]["uid"])
    except Exception:
        # Identity is reporting metadata, never a reason to invent an account
        # or discard a successfully fetched balance snapshot.
        pass


def optional_decimal(value):
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() else None
    except InvalidOperation:
        return None


def record_balance_details(connector, details):
    connector.account_balance_observations[details["ccy"]] = {
        "cash_balance": optional_decimal(details.get("cashBal")),
        "available_cash": optional_decimal(details.get("availBal")),
        "equity": optional_decimal(details.get("eq")),
        "available_equity": optional_decimal(details.get("availEq")),
        "observed_at": time.time(),
        "source": "okx_account_balance",
    }
