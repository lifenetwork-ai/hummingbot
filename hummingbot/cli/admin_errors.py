"""Sanitized HTTP error presentation; never serialize exception inputs/messages."""
import re
import uuid

from aiohttp import web

from hummingbot.cli.admin_models import ErrorResponse
from hummingbot.core.recovery_messages import RECOVERY_MESSAGES

CLEANUP_MESSAGES = {
    "cleanup_operation_changed": ("refresh_state", "The cleanup operation changed. Refresh its progress before trying again."),
    "operation_version_changed": ("refresh_state", "Cleanup progress changed. Refresh and review its latest state."),
    "credential_version_replaced": ("refresh_state", "A newer cleanup key replaced this request. Read the current cleanup operation."),
    "cleanup_orders_pending": ("wait", "Waiting for confirmed order outcomes. Keep the saved connection available."),
    "cleanup_ledger_pending": ("wait", "Waiting for fills and position checkpoints to agree with the exchange."),
    "cleanup_runtime_unavailable": ("wait", "The cleanup runtime is temporarily unavailable. Trading remains stopped."),
    "cleanup_metadata_unavailable": ("wait", "Waiting for exchange instrument metadata before cleanup can continue."),
    "cleanup_price_unavailable": ("wait", "A current price could not be verified for position closure. Trading remains stopped."),
    "cleanup_runtime_stop_incomplete": ("contact_support", "The old runtime has not confirmed shutdown. Cleanup cannot safely continue yet."),
    "cleanup_checkpoint_unavailable": ("contact_support", "A previous runtime ended without a verified position checkpoint. Reconcile the exchange and ledger before retrying."),
    "cleanup_order_tracking_missing": ("contact_support", "A saved order lacks its recovery state. Reconcile its exchange outcome before retrying."),
    "cleanup_unknown_orders": ("contact_support", "Additional bot orders were found on the exchange. Review and reconcile them before retrying cleanup."),
    "cleanup_position_mismatch": ("contact_support", "Exchange positions differ from the bot's saved positions. Reconcile both before retrying."),
    "cleanup_position_invalid": ("contact_support", "A position cannot be safely interpreted for automatic closure. Technical review is required."),
    "cleanup_perpetual_credentials_required": ("contact_support", "Perpetual positions exist but a working perpetual connection is missing. Restore the required connection for cleanup."),
    "cleanup_credentials_missing": ("contact_support", "A connection needed to clean up existing orders or positions is missing."),
    "cleanup_uncloseable_residual": ("contact_support", "A remaining position is below exchange size limits or cannot be represented exactly. Resolve it on the exchange and reconcile the ledger."),
    "cleanup_scope_limit": ("contact_support", "The number of saved orders or positions exceeds automatic cleanup limits. Technical review is required."),
    "cleanup_verification_changed": ("refresh_state", "Cleanup evidence changed before removal. Credentials were retained; review the current operation."),
    "cleanup_failed": ("contact_support", "Cleanup could not be completed. Credentials were retained. Contact support with the operation ID."),
}

RETRIES = {
    "credential_load_failed": "contact_support", "credentials_not_configured": "fix_request",
    "credentials_managed_externally": "contact_support", "credential_operation_in_progress": "wait",
    "credential_version_conflict": "refresh_state", "bot_must_be_stopped": "refresh_state",
    "account_binding_required": "contact_support", "exchange_account_mismatch": "fix_request",
    "exchange_credentials_rejected": "fix_request", "exchange_permissions_required": "fix_request",
    "exchange_unavailable": "wait", "exchange_identity_unavailable": "wait",
    "unsupported_exchange": "fix_request", "unsupported_connector": "fix_request", "unsupported_cleanup_mode": "fix_request",
    "credential_decryption_failed": "contact_support", "kms_permission_denied": "contact_support",
    "kms_key_unavailable": "contact_support", "kms_configuration_invalid": "contact_support", "kms_unavailable": "wait",
    "bootstrap_required": "contact_support", "last_admin_required": "fix_request",
    "account_exists": "refresh_state", "account_unavailable": "refresh_state", "account_version_conflict": "refresh_state",
    "request_too_large": "fix_request",
    "basic_auth_disabled": "reconnect", "token_expired": "reconnect",
    "account_not_allowed": "contact_support", "account_disabled": "contact_support",
    "insufficient_role": "contact_support", "identity_binding_mismatch": "contact_support",
    "account_onboarding_required": "reconnect", "identity_provider_unavailable": "wait",
    "database_unavailable": "wait", "security_work_busy": "wait",
    'duration_mode_not_allowed': 'fix_request', 'duration_mode_conflict': 'fix_request',
    "validation_failed": "fix_request", "yaml_configuration_read_only": "fix_request",
    "duration_out_of_range": "fix_request", "profile_unavailable": "fix_request",
    "selection_invalid": "fix_request", "idempotency_key_required": "fix_request",
    "control_conflict": "refresh_state", "control_version_changed": "refresh_state",
    "session_changed": "refresh_state", "session_active": "refresh_state", "session_not_active": "refresh_state",
    "catalogue_changed": "refresh_state", "idempotency_key_reused": "fix_request",
    "environment_mismatch": "contact_support", "owner_unavailable": "wait",
    "recovery_required": "contact_support", "drawdown_policy_bound": "contact_support",
    'automatic_recovery_in_progress': 'wait',
    "bootstrap_policy_bound": "contact_support", "outflow_policy_bound": "contact_support",
    'volatility_pause_policy_bound': 'contact_support',
    "check_in_progress": "wait", "check_cooldown": "wait",
    "unavailable": "same_request", "internal_error": "contact_support",
    "authentication_required": "reconnect", "origin_not_allowed": "contact_support",
    "invalid_json": "fix_request", "unsupported_media_type": "fix_request",
}

MESSAGES = {
    "credential_load_failed": "The engine could not load the saved connection. Contact support with the operation ID.",
    "credentials_not_configured": "Configure the required exchange connection before starting trading.",
    "credentials_managed_externally": "This connection is managed by deployment settings. Ask your maintainer to update it.",
    "credential_operation_in_progress": "A connection operation is in progress. Wait for its confirmed outcome.",
    "credential_version_conflict": "This connection changed. Refresh its status before submitting a new request.",
    "bot_must_be_stopped": "Stop trading and wait for confirmed order cleanup before changing this connection.",
    "account_binding_required": "Existing trading history needs an operator-reviewed account binding before this connection can be saved.",
    "exchange_account_mismatch": "This key belongs to a different exchange account. Use a key for this bot's account.",
    "exchange_credentials_rejected": "The exchange did not accept the connection details. Check the required credentials and environment.",
    "exchange_permissions_required": "Use a key with Read and Trade permissions, without Withdraw permission.",
    "exchange_unavailable": "The exchange could not be reached or checked. Wait and retry; this does not mean your key is invalid.",
    "exchange_identity_unavailable": "The exchange account identity could not be confirmed. Wait and retry.",
    "unsupported_exchange": "This exchange is not enabled in this deployment.",
    "unsupported_connector": "This connector is not enabled for the selected exchange and deployment.",
    "unsupported_cleanup_mode": "Perpetual connection currently requires a Futures or multi-currency account with Hedge mode and CROSS configuration.",
    "credential_decryption_failed": "The saved connection cannot be loaded. Contact support.",
    "kms_permission_denied": "The service cannot access its encryption key. Contact support.",
    "kms_key_unavailable": "The configured encryption key is unavailable. Contact support.",
    "kms_configuration_invalid": "Connection encryption is not configured correctly. Contact support.",
    "kms_unavailable": "Connection encryption is temporarily unavailable. Wait and retry with the same request identity.",
    "bootstrap_required": "Administrator setup is incomplete. Ask your maintainer to enable bootstrap access and finish Google onboarding.",
    "last_admin_required": "Keep at least one active Google administrator.",
    "account_exists": "This account is already approved. Refresh the account list.",
    "account_unavailable": "This account is no longer available. Refresh the account list.",
    "account_version_conflict": "This account changed. Refresh and review its current role before trying again.",
    "request_too_large": "This request is too large. Reduce its size and try again.",
    "basic_auth_disabled": "Password sign-in is disabled. Sign in with Google.",
    "token_expired": "Your sign-in expired. Sign in again to continue.",
    "account_not_allowed": "Your account is not allowed to use this dashboard. Contact an administrator.",
    "account_disabled": "Your dashboard access is disabled. Contact an administrator.",
    "insufficient_role": "Your role does not allow this action. Contact an administrator.",
    "identity_binding_mismatch": "This sign-in does not match the approved account. Contact an administrator.",
    "account_onboarding_required": "Sign in to activate your approved dashboard account.",
    "identity_provider_unavailable": "Sign-in verification is temporarily unavailable. Please try again shortly.",
    "database_unavailable": "Account access cannot be checked right now. Please try again shortly.",
    "security_work_busy": "Security work is busy. Please wait before trying again.",
    'duration_mode_not_allowed': 'Until stopped is not enabled in the deployed configuration. Choose Timed or contact your maintainer.',
    'duration_mode_conflict': 'Choose a duration for Timed, or omit it for Until stopped.',
    "validation_failed": "Check the highlighted fields and try again.",
    "yaml_configuration_read_only": "Profile settings are managed in YAML. Choose a configured profile instead.",
    "duration_out_of_range": "Choose a duration within the configured limits.",
    "profile_unavailable": "This profile is not available. Refresh the profile list.",
    "selection_invalid": "Choose one configured profile, or select Random without a profile.",
    "idempotency_key_required": "The request is missing its retry identity. Refresh the page and try again.",
    "control_version_changed": "The bot state changed. Review the latest state before submitting a new action.",
    "session_changed": "The active session changed. Review the current session.",
    "session_active": "A session is already active. Use Switch or Stop.",
    "session_not_active": "There is no active session to switch. Start a new session instead.",
    "catalogue_changed": "The available configuration changed. Refresh and review it before starting.",
    "idempotency_key_reused": "This retry identity belongs to a different action. Do not retry with a changed request.",
    "recovery_required": "Trading is blocked pending a technical review. Contact your support operator.",
    'automatic_recovery_in_progress': 'The service is verifying an uncertain exchange order. Wait for the result; Stop remains available.',
    "spot_outflow_checkpoint_invalid": "Saved spot risk information cannot be verified. Contact support before starting trading.",
    "drawdown_checkpoint_invalid": "Saved drawdown risk information cannot be verified. Contact support before starting trading.",
    "drawdown_policy_bound": "The saved risk limits differ from this deployment. A technical review is required.",
    "bootstrap_policy_bound": "The saved listing policy differs from this deployment. A technical review is required.",
    "outflow_policy_bound": "The saved spending policy differs from this deployment. A technical review is required.",
    'volatility_pause_policy_bound': 'A volatility pause is active and its YAML policy changed. Ask your maintainer to restore the saved policy and wait for recovery.',
    "check_in_progress": "A check is already running. Wait for its result.",
    "check_cooldown": "Please wait before starting another check.",
    "unavailable": "The service cannot confirm this request. Keep its retry identity and check again.",
    "internal_error": "The request could not be completed. Contact support with the request ID.",
    "authentication_required": "Sign in to continue.",
    "origin_not_allowed": "This dashboard address is not allowed. Contact support.",
    "invalid_json": "The request format is invalid.", "unsupported_media_type": "Send this request as JSON.",
    "control_conflict": "The action conflicts with the current bot state. Refresh and review the state.",
    "environment_mismatch": "The active owner uses a different trading environment. Contact support before starting.",
    "owner_unavailable": "Wait for the trading service to become available.",
}

RETRIES.update({code: value[0] for code, value in CLEANUP_MESSAGES.items()})
MESSAGES.update({code: value[1] for code, value in CLEANUP_MESSAGES.items()})
RETRIES.update({code: value[0] for code, value in RECOVERY_MESSAGES.items()})
MESSAGES.update({code: value[1] for code, value in RECOVERY_MESSAGES.items()})


def request_id(request):
    # Generate locally: arbitrary client headers must not become log identities.
    if "admin_request_id" not in request:
        request["admin_request_id"] = uuid.uuid4().hex
    return request["admin_request_id"]


def error_response(request, error, *, status, code=None, fields=(), retry_after_seconds=None, service=None):
    identity = request_id(request)
    code = code if code in RETRIES else error
    model = ErrorResponse(error=error, code=code, request_id=identity, retry=RETRIES.get(code),
                          detail=MESSAGES.get(code), fields=list(fields), retry_after_seconds=retry_after_seconds, service=service)
    headers = {"X-Request-ID": identity, "Cache-Control": "no-store"}
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(retry_after_seconds)
    return web.json_response(model.model_dump(mode="json", exclude_none=True), status=status, headers=headers)


def validation_fields(error):
    from controllers.market_making.life_mm import LifeMMConfig
    from hummingbot.client.config.mm_profiles import MMProfilesConfig
    readonly = set(LifeMMConfig.model_fields) | set(MMProfilesConfig.model_fields) | {
        "references", "profile_ids", "step_duration_seconds", "catalogue"}
    fields = []
    for item in error.errors(include_url=False, include_context=False, include_input=False)[:20]:
        location = list(item["loc"])
        if location and location[0] in ("start", "switch", "stop"):
            location.pop(0)
        path = ".".join(str(part) if isinstance(part, int) or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,79}", str(part))
                        else "unknown_field" for part in location) or "request"
        code = "yaml_configuration_read_only" if item["type"] == "extra_forbidden" and location and location[0] in readonly else item["type"]
        fields.append(dict(path=path[:256], code=code))
    return fields
