"""Shared OKX order-status decoding for the existing lost-order lifecycle."""
import json


class OKXOrderNotFoundError(IOError):
    """An explicit 51603 response, not a transport failure or missing payload."""


class OKXExpiredOrderRejection(IOError):
    """A correlated OKX response proving this request expired before admission."""

    def __init__(self, client_order_id):
        super().__init__("OKX rejected the expired order request (50036).")
        self.client_order_id = client_order_id


def check_order_submission(response, client_order_id):
    """Classify only an unambiguous, explicitly correlated expiry response."""
    if isinstance(response, dict) and str(response.get("code")) in ("0", "1"):
        rows = response.get("data")
        if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
            row = rows[0]
            if (str(row.get("sCode")) == "50036" and row.get("clOrdId") == client_order_id
                    and row.get("ordId") == ""):
                raise OKXExpiredOrderRejection(client_order_id)
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
