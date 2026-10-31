"""Shared OKX order-status decoding for the existing lost-order lifecycle."""
import json


class OKXOrderNotFoundError(IOError):
    """An explicit 51603 response, not a transport failure or missing payload."""


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
