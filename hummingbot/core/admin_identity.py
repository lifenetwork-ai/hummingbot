"""Verified administration identities; shared by HTTP and persistence adapters."""
import re
from dataclasses import dataclass
from typing import Optional

ROLES = {"read": 0, "write": 1, "admin": 2}


def normalize_email(value):
    if not isinstance(value, str):
        raise ValueError("Enter a valid Google account email address")
    email = value.strip().casefold()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Enter a valid Google account email address")
    return email


class AccessDenied(RuntimeError):
    def __init__(self, code="authentication_required", *, status=401, actor=None):
        super().__init__(code)
        self.code, self.status = code, status
        self.actor = actor


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
