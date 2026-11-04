"""Public identity/account/audit contracts; never credential or token payloads."""
from datetime import timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, field_validator, model_validator

from hummingbot.cli.admin_models import APIModel, PageQuery, UTCDateTime
from hummingbot.core.admin_identity import normalize_email


class PrincipalResponse(APIModel):
    id: str
    kind: Literal["basic", "google"]
    role: Literal["admin", "write", "read"]
    role_label: str
    email: Optional[str] = None
    permissions: Dict[str, bool]


class BootstrapResponse(APIModel):
    basic_auth_enabled: bool
    google_auth_enabled: bool
    google_admin_ready: bool
    active_google_admins: int
    detail: str


class AddAccountRequest(APIModel):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["admin", "write", "read"]
    active: bool = Field(default=True, strict=True)

    @field_validator("email")
    @classmethod
    def google_email(cls, value):
        return normalize_email(value)


class ChangeAccountRequest(APIModel):
    expected_version: int = Field(ge=1, strict=True)
    role: Optional[Literal["admin", "write", "read"]] = None
    active: Optional[bool] = Field(default=None, strict=True)

    @model_validator(mode="after")
    def has_change(self):
        if self.role is None and self.active is None:
            raise ValueError("Select a role or access change")
        return self


class AccountResponse(APIModel):
    id: str
    email: str
    role: Literal["admin", "write", "read"]
    active: bool
    bound: bool
    version: int
    created_at: UTCDateTime
    updated_at: UTCDateTime


class AccountsPage(APIModel):
    items: List[AccountResponse]
    next_cursor: Optional[str]


class AuditQuery(PageQuery):
    from_time: Optional[UTCDateTime] = None
    to_time: Optional[UTCDateTime] = None
    actor_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    action: Optional[str] = Field(default=None, min_length=1, max_length=80)
    outcome: Optional[str] = Field(default=None, min_length=1, max_length=80)
    target_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    operation_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    exchange: Optional[str] = Field(default=None, min_length=1, max_length=80)
    connector: Optional[str] = Field(default=None, min_length=1, max_length=80)
    environment: Optional[str] = Field(default=None, min_length=1, max_length=80)

    @field_validator("from_time", "to_time")
    @classmethod
    def aware_time(cls, value):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("Include a timezone in audit filters")
            return value.astimezone(timezone.utc)
        return value

    @model_validator(mode="after")
    def time_range(self):
        if self.from_time is not None and self.to_time is not None and self.from_time >= self.to_time:
            raise ValueError("Audit end time must follow start time")
        return self


class AuditEventResponse(APIModel):
    id: str
    request_id: str
    operation_id: Optional[str]
    actor_kind: str
    actor_id: Optional[str]
    actor_email: Optional[str]
    actor_role: Optional[str]
    action: str
    target_id: Optional[str]
    exchange: Optional[str]
    connector: Optional[str]
    environment: Optional[str]
    outcome: str
    reason_code: Optional[str]
    details: Dict[str, Any]
    created_at: UTCDateTime


class AuditPage(APIModel):
    items: List[AuditEventResponse]
    next_cursor: Optional[str]
