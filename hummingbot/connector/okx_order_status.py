"""Shared OKX order-status decoding for the existing lost-order lifecycle."""
import json

_EXPIRED_ORDER_CODES = frozenset({"50036", "50037"})
_PRICE_LIMIT_ORDER_CODES = frozenset({"51006", "51137", "51138"})
REVIEWED_ORDER_REJECTION_CODES = _EXPIRED_ORDER_CODES | _PRICE_LIMIT_ORDER_CODES


class OKXOrderNotFoundError(IOError):
    """An explicit 51603 response, not a transport failure or missing payload."""


class OKXOrderRejection(IOError):
    """An explicitly correlated order rejection without venue admission."""

    def __init__(self, client_order_id, message):
        super().__init__(message)
        self.client_order_id = client_order_id


class OKXExpiredOrderRejection(OKXOrderRejection):
    """A correlated OKX response proving this request expired before admission."""

    reason_code = "exchange_expired_request"

    def __init__(self, client_order_id, code="50036"):
        super().__init__(client_order_id, f"OKX rejected the expired order request ({code}).")


class OKXPriceLimitOrderRejection(OKXOrderRejection):
    """OKX explicitly rejected the requested price before accepting the order."""

    reason_code = "exchange_price_limit"

    def __init__(self, client_order_id, code="51006"):
        super().__init__(client_order_id, f"OKX rejected the order price ({code}).")


def check_order_submission(response, client_order_id):
    """Classify only reviewed rejection codes with unambiguous order identity."""
    if isinstance(response, dict) and str(response.get("code")) in ("0", "1"):
        rows = response.get("data")
        if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
            row = rows[0]
            if row.get("clOrdId") == client_order_id and row.get("ordId") == "":
                code = str(row.get("sCode"))
                if code in _EXPIRED_ORDER_CODES:
                    raise OKXExpiredOrderRejection(client_order_id, code)
                if code in _PRICE_LIMIT_ORDER_CODES:
                    raise OKXPriceLimitOrderRejection(client_order_id, code)
    raise IOError("OKX order submission failed or returned an ambiguous response.")


def order_status_data(response):
    if not isinstance(response, dict):
        raise IOError("Malformed OKX order-status response.")
    code, rows = str(response.get("code")), response.get("data")
    if code == "51603" and rows == []:
        raise OKXOrderNotFoundError("OKX order does not exist (51603).")
    if code != "0":
        # Preserve the existing time-synchronizer error-code format, without
        # echoing exchange messages or order/account payloads into logs.
        raise IOError("OKX order-status request failed: " + json.dumps({"code": code}, separators=(",", ":")))
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise IOError("Malformed OKX order-status data.")
    return rows[0]


def order_rejection_reason(receipt, client_order_id):
    """Only a persisted HTTP 200 receipt with exact venue rejection is proof."""
    if not isinstance(receipt, dict) or type(receipt.get('http_status')) is not int or receipt['http_status'] != 200:
        return None
    try:
        check_order_submission({'code': receipt.get('code'), 'data': [receipt.get('order')]}, client_order_id)
    except OKXOrderRejection as rejection:
        return rejection.reason_code
    except IOError:
        return None
