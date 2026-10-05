"""Fail-closed OKX account observations for LIFE spot and perpetual markets.

Account equity belongs to one OKX UID even when two Hummingbot connectors
observe it. Positions are exposure, not another collateral balance.
"""

import math
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Callable, Mapping

ACCOUNT_CONFIG_PATH = "/api/v5/account/config"
ACCOUNT_BALANCE_PATH = "/api/v5/account/balance"
ACCOUNT_POSITIONS_PATH = "/api/v5/account/positions"


class AccountDataError(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _response_rows(response: Any) -> list[dict[str, Any]]:
    if (not isinstance(response, dict) or response.get("code") != "0"
            or not isinstance(response.get("data"), list)
            or any(not isinstance(row, dict) for row in response["data"])):
        raise AccountDataError("ACCOUNT_RESPONSE_INVALID")
    return response["data"]


def _milliseconds(value: Any) -> int:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,20}", value) is None:
        raise AccountDataError("ACCOUNT_TIMESTAMP_INVALID")
    timestamp = int(value)
    if timestamp <= 0:
        raise AccountDataError("ACCOUNT_TIMESTAMP_INVALID")
    return timestamp


def _decimal(value: Any, reason_code: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise AccountDataError(reason_code)
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise AccountDataError(reason_code) from exc
    if not amount.is_finite():
        raise AccountDataError(reason_code)
    return amount


def _receipt_time(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise AccountDataError("ACCOUNT_RECEIPT_TIME_INVALID")
    return float(value)


@dataclass(frozen=True)
class SnapshotQuality:
    source: str
    exchange_timestamp_ms: int
    received_monotonic: float
    state: str = "VALIDATED"
    timestamp_kind: str = "UPDATE"

    def is_fresh(self, *, exchange_now_ms: int, monotonic_now: float,
                 max_exchange_age_ms: int, max_receipt_age_seconds: float) -> bool:
        if (isinstance(exchange_now_ms, bool) or not isinstance(exchange_now_ms, int)
                or isinstance(max_exchange_age_ms, bool) or not isinstance(max_exchange_age_ms, int)
                or max_exchange_age_ms <= 0 or isinstance(monotonic_now, bool)
                or not isinstance(monotonic_now, (int, float)) or not math.isfinite(monotonic_now)
                or isinstance(max_receipt_age_seconds, bool)
                or not isinstance(max_receipt_age_seconds, (int, float))
                or not math.isfinite(max_receipt_age_seconds) or max_receipt_age_seconds <= 0):
            return False
        exchange_age = exchange_now_ms - self.exchange_timestamp_ms
        receipt_age = monotonic_now - self.received_monotonic
        return (0 <= exchange_age <= max_exchange_age_ms
                and 0 <= receipt_age <= max_receipt_age_seconds)


@dataclass(frozen=True)
class AccountConfigSnapshot:
    account_id: str
    connector_name: str
    account_mode: str
    position_mode: str
    quality: SnapshotQuality

    @classmethod
    def from_okx(cls, response: Any, *, connector_name: str, expected_account_id: str,
                 observed_exchange_ms: int, received_monotonic: float) -> "AccountConfigSnapshot":
        rows = _response_rows(response)
        if len(rows) != 1:
            raise AccountDataError("ACCOUNT_CONFIG_INCOMPLETE")
        row = rows[0]
        account_id = row.get("uid")
        if not isinstance(expected_account_id, str) or not expected_account_id:
            raise AccountDataError("ACCOUNT_ID_MISSING")
        if account_id != expected_account_id:
            raise AccountDataError("ACCOUNT_ID_MISMATCH")
        if row.get("acctLv") not in ("1", "2", "3", "4") or row.get("posMode") not in (
                "net_mode", "long_short_mode"):
            raise AccountDataError("ACCOUNT_MODE_INVALID")
        if not isinstance(connector_name, str) or not connector_name:
            raise AccountDataError("ACCOUNT_CONNECTOR_UNAVAILABLE")
        quality = SnapshotQuality(f"{connector_name}:config", _milliseconds(str(observed_exchange_ms)),
                                  _receipt_time(received_monotonic), timestamp_kind="OBSERVED")
        return cls(account_id, connector_name, row["acctLv"], row["posMode"], quality)


@dataclass(frozen=True)
class BalanceSnapshot:
    account_id: str
    connector_name: str
    assets: Mapping[str, Decimal]
    total_equity_usd: Decimal
    collateral_equity_usd: Decimal | None
    quality: SnapshotQuality

    @classmethod
    def from_okx(cls, response: Any, *, identity: AccountConfigSnapshot,
                 required_currencies: tuple[str, ...], received_monotonic: float) -> "BalanceSnapshot":
        rows = _response_rows(response)
        if len(rows) != 1:
            raise AccountDataError("BALANCE_SNAPSHOT_INCOMPLETE")
        row = rows[0]
        details = row.get("details")
        if not isinstance(details, list) or any(not isinstance(item, dict) for item in details):
            raise AccountDataError("BALANCE_SNAPSHOT_INCOMPLETE")
        updated = _milliseconds(row.get("uTime"))
        total = _decimal(row.get("totalEq"), "BALANCE_AMOUNT_INVALID")
        adjusted = row.get("adjEq")
        collateral = (_decimal(adjusted, "BALANCE_AMOUNT_INVALID")
                      if adjusted not in (None, "") else None)
        if total < 0 or collateral is not None and collateral < 0:
            raise AccountDataError("BALANCE_AMOUNT_INVALID")
        assets: dict[str, Decimal] = {}
        oldest_update = updated
        for item in details:
            currency = item.get("ccy")
            if not isinstance(currency, str) or re.fullmatch(r"[A-Z0-9]{2,20}", currency) is None:
                raise AccountDataError("BALANCE_CURRENCY_INVALID")
            if currency in assets:
                raise AccountDataError("BALANCE_SNAPSHOT_CONFLICT")
            assets[currency] = _decimal(item.get("eq"), "BALANCE_AMOUNT_INVALID")
            item_update = _milliseconds(item.get("uTime"))
            if item_update > updated:
                raise AccountDataError("ACCOUNT_TIMESTAMP_INVALID")
            oldest_update = min(oldest_update, item_update)
        if (not required_currencies or any(currency not in assets for currency in required_currencies)):
            raise AccountDataError("BALANCE_CURRENCY_MISSING")
        quality = SnapshotQuality(f"{identity.connector_name}:balance", oldest_update,
                                  _receipt_time(received_monotonic))
        return cls(identity.account_id, identity.connector_name, MappingProxyType(assets),
                   total, collateral, quality)


@dataclass(frozen=True)
class PositionSnapshot:
    account_id: str
    connector_name: str
    instrument_id: str
    positions_by_side: Mapping[str, Decimal]
    signed_contracts: Decimal
    quality: SnapshotQuality

    @classmethod
    def from_okx(cls, response: Any, *, identity: AccountConfigSnapshot,
                 instrument_id: str, received_monotonic: float) -> "PositionSnapshot":
        rows = _response_rows(response)
        if not rows:
            raise AccountDataError("POSITION_SNAPSHOT_INCOMPLETE")
        sides: dict[str, Decimal] = {}
        timestamps = []
        for row in rows:
            if row.get("instType") != "SWAP" or row.get("instId") != instrument_id:
                raise AccountDataError("POSITION_INSTRUMENT_MISMATCH")
            side = row.get("posSide")
            if side not in ("net", "long", "short"):
                raise AccountDataError("POSITION_MODE_MISMATCH")
            if side in sides:
                raise AccountDataError("POSITION_SNAPSHOT_CONFLICT")
            amount = _decimal(row.get("pos"), "POSITION_AMOUNT_INVALID")
            if side != "net" and amount < 0:
                raise AccountDataError("POSITION_AMOUNT_INVALID")
            sides[side] = amount
            timestamps.append(_milliseconds(row.get("uTime")))
        expected_sides = ({"net"} if identity.position_mode == "net_mode"
                          else {"long", "short"})
        if set(sides) != expected_sides:
            raise AccountDataError("POSITION_MODE_MISMATCH")
        signed = sides["net"] if "net" in sides else sides["long"] - sides["short"]
        quality = SnapshotQuality(f"{identity.connector_name}:positions", min(timestamps),
                                  _receipt_time(received_monotonic))
        return cls(identity.account_id, identity.connector_name, instrument_id,
                   MappingProxyType(sides), signed, quality)


@dataclass(frozen=True)
class UnifiedAccountSnapshot:
    account_id: str
    identities: tuple[AccountConfigSnapshot, ...]
    balances: tuple[BalanceSnapshot, ...]
    positions: PositionSnapshot | None
    balance_source: str
    assets: Mapping[str, Decimal]
    total_equity_usd: Decimal
    collateral_equity_usd: Decimal | None

    @classmethod
    def combine(cls, *, identities: tuple[AccountConfigSnapshot, ...],
                balances: tuple[BalanceSnapshot, ...],
                positions: PositionSnapshot | None) -> "UnifiedAccountSnapshot":
        if not identities or not balances:
            raise AccountDataError("ACCOUNT_SNAPSHOT_INCOMPLETE")
        account_id = identities[0].account_id
        if (any(identity.account_id != account_id for identity in identities)
                or any(balance.account_id != account_id for balance in balances)
                or positions is not None and positions.account_id != account_id):
            raise AccountDataError("ACCOUNT_ID_MISMATCH")
        by_connector = {identity.connector_name: identity for identity in identities}
        has_unknown_balance_source = any(balance.connector_name not in by_connector for balance in balances)
        if len(by_connector) != len(identities) or has_unknown_balance_source:
            raise AccountDataError("ACCOUNT_SNAPSHOT_INCOMPLETE")
        if any(identity.account_mode != identities[0].account_mode
               or identity.position_mode != identities[0].position_mode for identity in identities):
            raise AccountDataError("ACCOUNT_MODE_MISMATCH")
        if positions is not None and positions.connector_name not in by_connector:
            raise AccountDataError("ACCOUNT_SNAPSHOT_INCOMPLETE")
        ordered = sorted(balances, key=lambda item: item.quality.exchange_timestamp_ms, reverse=True)
        chosen = ordered[0]
        for other in ordered[1:]:
            if (other.quality.exchange_timestamp_ms == chosen.quality.exchange_timestamp_ms
                    and (other.total_equity_usd != chosen.total_equity_usd
                         or other.collateral_equity_usd != chosen.collateral_equity_usd
                         or dict(other.assets) != dict(chosen.assets))):
                raise AccountDataError("BALANCE_SNAPSHOT_CONFLICT")
        return cls(account_id, identities, balances, positions, chosen.connector_name,
                   chosen.assets, chosen.total_equity_usd, chosen.collateral_equity_usd)

    def is_fresh(self, *, exchange_now_ms: int, monotonic_now: float,
                 max_exchange_age_ms: int, max_receipt_age_seconds: float) -> bool:
        observations = [identity.quality for identity in self.identities]
        observations.extend(balance.quality for balance in self.balances)
        if self.positions is not None:
            observations.append(self.positions.quality)
        return all(quality.is_fresh(exchange_now_ms=exchange_now_ms, monotonic_now=monotonic_now,
                                    max_exchange_age_ms=max_exchange_age_ms,
                                    max_receipt_age_seconds=max_receipt_age_seconds)
                   for quality in observations)


class OkxAccountSnapshotSource:
    """Collect read-only private snapshots; UID binding comes from account/config."""

    def __init__(self, market_data_provider, *, expected_account_id: str,
                 include_spot: bool = True, include_perpetual: bool = False,
                 clock: Callable[[], float] = time.monotonic):
        if not isinstance(expected_account_id, str) or not expected_account_id:
            raise AccountDataError("ACCOUNT_ID_MISSING")
        if not include_spot and not include_perpetual:
            raise AccountDataError("ACCOUNT_SNAPSHOT_INCOMPLETE")
        self.market_data_provider = market_data_provider
        self.expected_account_id = expected_account_id
        self.connectors = (("okx",) if include_spot else ()) + (("okx_perpetual",) if include_perpetual else ())
        self.include_perpetual = include_perpetual
        self.clock = clock

    def _connector(self, name: str):
        registered = getattr(self.market_data_provider, "connectors", None)
        connector = registered.get(name) if isinstance(registered, dict) else None
        if connector is None or not callable(getattr(connector, "_api_get", None)):
            raise AccountDataError("ACCOUNT_CONNECTOR_UNAVAILABLE")
        return connector

    async def fetch(self, *, observed_exchange_ms: int) -> UnifiedAccountSnapshot:
        identities = []
        for name in self.connectors:
            response = await self._connector(name)._api_get(
                path_url=ACCOUNT_CONFIG_PATH, is_auth_required=True)
            received_monotonic = self.clock()
            identities.append(AccountConfigSnapshot.from_okx(
                response, connector_name=name, expected_account_id=self.expected_account_id,
                observed_exchange_ms=observed_exchange_ms, received_monotonic=received_monotonic))
        balances = []
        for identity in identities:
            response = await self._connector(identity.connector_name)._api_get(
                path_url=ACCOUNT_BALANCE_PATH, params={"ccy": "LIFE,USDT"}, is_auth_required=True)
            received_monotonic = self.clock()
            balances.append(BalanceSnapshot.from_okx(
                response, identity=identity, required_currencies=("LIFE", "USDT"),
                received_monotonic=received_monotonic))
        positions = None
        if self.include_perpetual:
            identity = next(item for item in identities if item.connector_name == "okx_perpetual")
            response = await self._connector(identity.connector_name)._api_get(
                path_url=ACCOUNT_POSITIONS_PATH,
                params={"instType": "SWAP", "instId": "LIFE-USDT-SWAP"}, is_auth_required=True)
            received_monotonic = self.clock()
            positions = PositionSnapshot.from_okx(
                response, identity=identity, instrument_id="LIFE-USDT-SWAP",
                received_monotonic=received_monotonic)
        return UnifiedAccountSnapshot.combine(
            identities=tuple(identities), balances=tuple(balances), positions=positions)


class AccountSnapshotGate:
    """A failed or stale refresh revokes the previous observation."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.snapshot: UnifiedAccountSnapshot | None = None
        self.reason_code = "ACCOUNT_SNAPSHOT_UNCHECKED"
        self.clock = clock
        self._refresh_revision = 0

    def permit(self, *, exchange_now_ms: int, monotonic_now: float,
               max_exchange_age_ms: int, max_receipt_age_seconds: float) -> bool:
        return self.snapshot is not None and self.snapshot.is_fresh(
            exchange_now_ms=exchange_now_ms, monotonic_now=monotonic_now,
            max_exchange_age_ms=max_exchange_age_ms,
            max_receipt_age_seconds=max_receipt_age_seconds)

    async def refresh(self, source, *, observed_exchange_ms: int,
                      max_exchange_age_ms: int, max_receipt_age_seconds: float) -> bool:
        self._refresh_revision += 1
        revision = self._refresh_revision
        self.snapshot = None
        try:
            snapshot = await source.fetch(observed_exchange_ms=observed_exchange_ms)
        except AccountDataError as exc:
            if revision == self._refresh_revision:
                self.reason_code = exc.reason_code
            return False
        except Exception:
            if revision == self._refresh_revision:
                self.reason_code = "ACCOUNT_FETCH_ERROR"
            return False
        if revision != self._refresh_revision:
            return False
        if not snapshot.is_fresh(exchange_now_ms=observed_exchange_ms,
                                 monotonic_now=self.clock(),
                                 max_exchange_age_ms=max_exchange_age_ms,
                                 max_receipt_age_seconds=max_receipt_age_seconds):
            self.reason_code = "ACCOUNT_SNAPSHOT_STALE"
            return False
        self.snapshot = snapshot
        self.reason_code = "ACCOUNT_SNAPSHOT_VALID"
        return True
