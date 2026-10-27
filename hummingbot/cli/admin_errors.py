"""Sanitized HTTP error presentation; never serialize exception inputs/messages."""
import re
import uuid

from aiohttp import web

from hummingbot.cli.admin_models import ErrorResponse

RETRIES = {
    'duration_mode_not_allowed': 'fix_request', 'duration_mode_conflict': 'fix_request',
    "validation_failed": "fix_request", "yaml_configuration_read_only": "fix_request",
    "duration_out_of_range": "fix_request", "profile_unavailable": "fix_request",
    "selection_invalid": "fix_request", "idempotency_key_required": "fix_request",
    "control_conflict": "refresh_state", "control_version_changed": "refresh_state",
    "session_changed": "refresh_state", "session_active": "refresh_state", "session_not_active": "refresh_state",
    "catalogue_changed": "refresh_state", "idempotency_key_reused": "fix_request",
    "environment_mismatch": "contact_support", "owner_unavailable": "wait",
    "recovery_required": "contact_support", "drawdown_policy_bound": "contact_support",
    "bootstrap_policy_bound": "contact_support", "outflow_policy_bound": "contact_support",
    "check_in_progress": "wait", "check_cooldown": "wait",
    "unavailable": "same_request", "internal_error": "contact_support",
    "authentication_required": "reconnect", "origin_not_allowed": "contact_support",
    "invalid_json": "fix_request", "unsupported_media_type": "fix_request",
}

MESSAGES = {
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
    "drawdown_policy_bound": "The saved risk limits differ from this deployment. A technical review is required.",
    "bootstrap_policy_bound": "The saved listing policy differs from this deployment. A technical review is required.",
    "outflow_policy_bound": "The saved spending policy differs from this deployment. A technical review is required.",
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
