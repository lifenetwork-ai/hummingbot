"""Bounded, sanitized incident state for the existing service owner loop."""
import logging
import time

from hummingbot.core.utils.read_retry import retry_delay


class ServiceAvailability:
    def __init__(self):
        self.incidents = {}
        self.healthy = {}
        self.logged = {}

    def fail(self, scope, reason, error=None, *, operator=False, retry=False):
        now = time.time()
        if scope not in self.incidents and len(self.incidents) >= 15:
            scope = "other"
        previous = self.incidents.get(scope, {})
        count = min(previous.get("retry_count", 0) + 1, 60)
        delay = retry_delay(count, error) if retry else None
        self.incidents[scope] = dict(
            reason_code=reason, scope=scope, severity="error" if operator else "warning",
            detail=type(error).__name__ if error is not None else None,
            first_seen_at=previous.get("first_seen_at", now), last_seen_at=now,
            last_healthy_at=self.healthy.get(scope), retry_count=count,
            next_retry_at=now + delay if delay is not None else None,
            active_fallback=None, operator_action_required=operator)
        if (previous.get("reason_code") != reason or previous.get("operator_action_required") != operator
                or time.monotonic() - self.logged.get(scope, 0) >= 60):
            logging.getLogger(__name__).log(
                logging.ERROR if operator else logging.WARNING,
                "Service incident scope=%s reason=%s type=%s operator=%s",
                scope, reason, type(error).__name__ if error else "-", operator,
                extra={"event": "service_incident", "scope": scope, "reason_code": reason,
                       "error_type": type(error).__name__ if error else "-"})
            self.logged[scope] = time.monotonic()
        return delay

    def clear(self, scope):
        if scope in self.incidents:
            logging.getLogger(__name__).info("Service observation recovered scope=%s", scope,
                                             extra={"event": "service_recovered", "scope": scope})
            del self.incidents[scope]
            self.logged.pop(scope, None)
        if scope in self.healthy or len(self.healthy) < 16:
            self.healthy[scope] = time.time()

    def snapshot(self):
        return {"incidents": [dict(value) for value in self.incidents.values()]}
