# Explicit operator inputs; the test profile is synthetic, never a live default.
variable "monitoring" {
  type = object({
    notification_channels       = set(string)
    unknown_attempts            = number
    unresolved_holds            = number
    budget_usage_ratio          = number
    event_loop_lag_ms           = number
    telemetry_max_age_ms        = number
    db_phase_ms                 = number
    heartbeat_absence_seconds   = number
    notification_period_seconds = number
  })
  validation {
    condition = length(var.monitoring.notification_channels) > 0 && alltrue([
      for c in var.monitoring.notification_channels : can(regex("^projects/[a-z0-9-]+/notificationChannels/[0-9]+$", c))
    ]) && var.monitoring.unknown_attempts >= 1 && var.monitoring.unknown_attempts <= 256 && floor(var.monitoring.unknown_attempts) == var.monitoring.unknown_attempts && var.monitoring.unresolved_holds >= 1 && var.monitoring.unresolved_holds <= 256 && floor(var.monitoring.unresolved_holds) == var.monitoring.unresolved_holds && var.monitoring.budget_usage_ratio > 0 && var.monitoring.budget_usage_ratio <= 1 && var.monitoring.event_loop_lag_ms > 0 && var.monitoring.event_loop_lag_ms <= 500 && var.monitoring.telemetry_max_age_ms >= 1000 && var.monitoring.telemetry_max_age_ms <= 60000 && var.monitoring.db_phase_ms > 0 && var.monitoring.db_phase_ms <= 60000 && var.monitoring.heartbeat_absence_seconds >= 120 && var.monitoring.heartbeat_absence_seconds <= 3600 && var.monitoring.notification_period_seconds >= 60 && var.monitoring.notification_period_seconds <= 3600
    error_message = "Supply routed notification channels and bounded, calibrated thresholds; test values are not live defaults."
  }
}

locals {
  worker_log_scope = "resource.type=\"cloud_run_worker_pool\" AND resource.labels.worker_pool_name=\"${local.prefix}\" AND resource.labels.location=\"${var.region}\" AND jsonPayload.component=\"life_worker\" AND jsonPayload.event=\"STATUS\""
  worker_alerts = {
    telemetry   = "(jsonPayload.reason_code!=\"STATUS_CURRENT\" OR jsonPayload.financial_metrics.state!=\"CURRENT\" OR jsonPayload.financial_metrics.sample_age_ms>${var.monitoring.telemetry_max_age_ms})"
    uncertainty = "(jsonPayload.financial_metrics.unknown_attempts.count>=${var.monitoring.unknown_attempts} OR jsonPayload.financial_metrics.unknown_attempts.truncated=true)"
    holds       = "(jsonPayload.financial_metrics.unresolved_holds.count>=${var.monitoring.unresolved_holds} OR jsonPayload.financial_metrics.unresolved_holds.truncated=true OR jsonPayload.financial_metrics.capital_claims.count>=${var.monitoring.unresolved_holds} OR jsonPayload.financial_metrics.capital_claims.truncated=true OR jsonPayload.financial_metrics.shared_claims.count>=${var.monitoring.unresolved_holds} OR jsonPayload.financial_metrics.shared_claims.truncated=true)"
    budget      = "jsonPayload.financial_metrics.budget_usage_ratio_max>=${var.monitoring.budget_usage_ratio}"
    latency     = "(jsonPayload.loop_lag_ms>=${var.monitoring.event_loop_lag_ms} OR jsonPayload.database_metrics.phase_max_ms.acquire>=${var.monitoring.db_phase_ms} OR jsonPayload.database_metrics.phase_max_ms.execute>=${var.monitoring.db_phase_ms} OR jsonPayload.database_metrics.phase_max_ms.commit>=${var.monitoring.db_phase_ms})"
    eligibility = "(jsonPayload.financial_metrics.mode=\"ACTIVE\" AND jsonPayload.local_trading_ready=false)"
    integrity   = "(jsonPayload.financial_metrics.audit_health!=\"VERIFIED\" OR jsonPayload.financial_metrics.backup_health!=\"VERIFIED\" OR jsonPayload.financial_metrics.recovery_health!=\"VERIFIED\" OR jsonPayload.financial_metrics.restore_quarantined=true)"
  }
}

resource "google_monitoring_alert_policy" "worker" {
  for_each              = local.worker_alerts
  project               = var.project_id
  display_name          = "${local.prefix}: ${each.key}"
  combiner              = "OR"
  enabled               = var.worker_instances == 1
  notification_channels = var.monitoring.notification_channels
  conditions {
    display_name = each.key
    condition_matched_log {
      filter = "${local.worker_log_scope} AND jsonPayload.started=true AND jsonPayload.stopping=false AND ${each.value}"
    }
  }
  alert_strategy {
    notification_rate_limit { period = "${var.monitoring.notification_period_seconds}s" }
    auto_close = "1800s"
  }
  documentation {
    mime_type = "text/markdown"
    content   = "Read the bounded status and original financial evidence. A healthy probe, cancellation ACK or alert closure does not authorize trading, release funds, or certify recovery. Unverified audit/backup health requires investigation; never treat missing evidence as success."
  }
}

resource "google_logging_metric" "heartbeat" {
  project = var.project_id
  name    = "${local.prefix}-heartbeat"
  filter  = local.worker_log_scope
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "heartbeat" {
  project               = var.project_id
  display_name          = "${local.prefix}: missing telemetry"
  combiner              = "OR"
  enabled               = var.worker_instances == 1
  notification_channels = var.monitoring.notification_channels
  conditions {
    display_name = "Worker status absent"
    condition_absent {
      filter   = "resource.type=\"cloud_run_worker_pool\" AND metric.type=\"logging.googleapis.com/user/${google_logging_metric.heartbeat.name}\""
      duration = "${var.monitoring.heartbeat_absence_seconds}s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
      trigger { count = 1 }
    }
  }
}

# Backup success is separate from proved restorability/reconciliation. The
# worker has no Cloud SQL administration or backup-reading permissions.
resource "google_monitoring_alert_policy" "backup_failure" {
  project               = var.project_id
  display_name          = "${local.prefix}: automated backup failed or skipped"
  combiner              = "OR"
  enabled               = true
  notification_channels = var.monitoring.notification_channels
  conditions {
    display_name = "Cloud SQL automated backup status"
    condition_matched_log {
      filter = "resource.type=\"cloudsql_database\" AND logName=\"projects/${var.project_id}/logs/cloudaudit.googleapis.com%2Fsystem_event\" AND protoPayload.methodName=\"cloudsql.instances.automatedBackup\" AND protoPayload.resourceName=\"projects/${var.project_id}/instances/${google_sql_database_instance.primary.name}\" AND protoPayload.metadata.windowStatus=(\"STATUS_FAILED\" OR \"STATUS_ATTEMPT_FAILED\" OR \"STATUS_SKIPPED\")"
    }
  }
  alert_strategy {
    notification_rate_limit { period = "${var.monitoring.notification_period_seconds}s" }
    auto_close = "86400s"
  }
}
