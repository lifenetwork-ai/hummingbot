"""Stable operator error identities without coupling storage to HTTP."""


class RequestError(ValueError):
    def __init__(self, message, *, code="validation_failed", field=None):
        super().__init__(message)
        self.code = code
        self.field = field


class ControlConflict(RuntimeError):
    code = "control_conflict"

    def __init__(self, message, *, code=None, retry_after_seconds=None):
        super().__init__(message)
        self.code = code or type(self).code
        self.retry_after_seconds = retry_after_seconds
