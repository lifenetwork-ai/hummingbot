"""Secret input is separate from receipt/status output by construction."""
from typing import Dict, List, Literal, Optional

from pydantic import Field, SecretStr

from hummingbot.cli.admin_models import APIModel, UTCDateTime


class CredentialSetRequest(APIModel):
    expected_version: int = Field(ge=0, strict=True)
    expected_control_version: int = Field(ge=0, strict=True)
    credentials: Dict[str, SecretStr] = Field(min_length=1, max_length=8, repr=False)


class CredentialDisconnectRequest(APIModel):
    expected_version: int = Field(ge=1, strict=True)
    expected_control_version: int = Field(ge=0, strict=True)


class CredentialRetryRequest(CredentialDisconnectRequest):
    expected_operation_version: int = Field(ge=0, strict=True)


class CredentialCleanupKeyRequest(CredentialRetryRequest):
    operation_id: str = Field(min_length=1, max_length=128)
    credentials: Dict[str, SecretStr] = Field(min_length=1, max_length=8, repr=False)


class CredentialPayload(APIModel):
    action: Literal["credentials_set", "credentials_disconnect", "credentials_cleanup_key", "credentials_cleanup_retry"]
    bot_id: str
    actor_id: str
    actor_kind: str
    exchange: str
    connector: str
    environment: str
    expected_version: int
    expected_control_version: int
    cleanup_operation_id: Optional[str] = None
    expected_operation_version: Optional[int] = None


class CredentialProgress(APIModel):
    stage: str
    credential_version: int
    reason_code: Optional[str] = None
    operator_action_required: bool = False
    owner_uuid: Optional[str] = None
    owner_generation: Optional[int] = None
    operation_version: int = 0
    cleanup_operation_id: Optional[str] = None
    account_verified: Optional[bool] = None
    position_checkpoints_verified: Optional[bool] = None
    orders_terminal_verified: Optional[bool] = None
    positions_flat_verified: Optional[bool] = None
    ledger_confirmed: Optional[bool] = None
    consumers_detached: Optional[bool] = None
    observed_at: Optional[UTCDateTime] = None
    next_retry_at: Optional[UTCDateTime] = None
    blocked_stage: Optional[str] = None
    remaining_order_count: Optional[int] = None
    remaining_position_count: Optional[int] = None


class CredentialReceipt(APIModel):
    id: str
    action: Literal["credentials_set", "credentials_disconnect", "credentials_cleanup_key", "credentials_cleanup_retry"]
    session_id: Optional[str] = None
    expected_version: int
    effective_version: int
    status: Literal["accepted", "applying", "succeeded", "failed"]
    payload: CredentialPayload
    result: CredentialProgress
    error: Optional[str] = None
    created_at: UTCDateTime
    updated_at: UTCDateTime


class CredentialInputField(APIModel):
    name: str
    label: str
    secret: bool
    required: bool


class ExchangeCredentialDescriptor(APIModel):
    exchange: str
    environment: str
    site: Optional[str] = None
    fields: List[CredentialInputField]
    cleanup_mode: str
    connectors: List[str]
    disconnect_available: bool = False


class CredentialCatalogue(APIModel):
    source: Literal["environment", "postgres_kms"]
    exchanges: List[ExchangeCredentialDescriptor]


class CredentialAction(APIModel):
    allowed: bool
    reason_code: Optional[str] = None
    detail: str


class CredentialSlot(APIModel):
    connector: str
    configured: bool
    version: int
    status: str
    label: str
    detail: str
    reason_code: Optional[str] = None
    changed_at: Optional[UTCDateTime] = None
    changed_by: Optional[str] = None
    operation_id: Optional[str] = None
    cleanup_operation_id: Optional[str] = None
    operation_version: Optional[int] = None
    observed_at: Optional[UTCDateTime] = None
    actions: Dict[str, CredentialAction]


class ExchangeCredentialStatus(APIModel):
    exchange: str
    environment: str
    source: Literal["environment", "postgres_kms"]
    slots: List[CredentialSlot]
