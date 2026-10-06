"""Spot quote slot identity backed by the durable intent journal."""

from dataclasses import dataclass

from hummingbot.strategy_v2.life_liquidity.state import IntentRecord, IntentWAL


@dataclass(frozen=True)
class SlotStatus:
    state: str
    intent_id: str | None = None


class SpotQuoteSlots:
    def __init__(self, wal: IntentWAL, market: str = "LIFE-USDT"):
        if not isinstance(market, str) or not market:
            raise ValueError("SLOT_MARKET_INVALID")
        self.wal = wal
        self.market = market

    @staticmethod
    def _validate_location(session_id: str, epoch: int, side: str, level: int) -> None:
        if (not isinstance(session_id, str) or not session_id
                or not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1
                or side not in ("BUY", "SELL")
                or not isinstance(level, int) or isinstance(level, bool) or level < 0):
            raise ValueError("SLOT_IDENTITY_INVALID")

    def claim(self, *, intent_id: str, client_order_id: str, reservation_id: str,
              session_id: str, epoch: int, side: str, level: int) -> IntentRecord:
        self._validate_location(session_id, epoch, side, level)
        self.wal.begin(intent_id, client_order_id=client_order_id,
                       reservation_id=reservation_id, session_id=session_id,
                       epoch=epoch, slot_market=self.market, slot_side=side,
                       slot_level=level)
        return self.wal.get(intent_id)

    def status(self, session_id: str, epoch: int, side: str, level: int) -> SlotStatus:
        self._validate_location(session_id, epoch, side, level)
        active = [record for record in self.wal.all_records()
                  if record.session_id == session_id and record.epoch == epoch
                  and (record.slot_market, record.slot_side, record.slot_level)
                  == (self.market, side, level)
                  and record.state not in ("TERMINAL", "ABORTED_BEFORE_SEND")]
        if len(active) > 1:
            raise ValueError("SLOT_JOURNAL_CONFLICT")
        if not active:
            return SlotStatus("FREE")
        record = active[0]
        if record.exchange_terminal_observed:
            state = "RECONCILIATION_PENDING"
        elif record.cancel_requested:
            state = "CANCEL_PENDING"
        elif record.state == "ACKED":
            state = "OPEN"
        elif record.state == "SEND_UNKNOWN":
            state = "UNKNOWN"
        else:
            state = record.state
        return SlotStatus(state, record.intent_id)
