"""Verified administration identities; shared by HTTP and persistence adapters."""
from dataclasses import dataclass
from typing import Optional

ROLES = {"read": 0, "write": 1, "admin": 2}


class AccessDenied(RuntimeError):
    def __init__(self, code="authentication_required", *, status=401):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Principal:
    id: str
    kind: str
    role: str
    email: Optional[str] = None
    account_version: Optional[int] = None

    def allows(self, required):
        return required in ROLES and ROLES.get(self.role, -1) >= ROLES[required]

    def require(self, required):
        if not self.allows(required):
            raise AccessDenied("insufficient_role", status=403)
