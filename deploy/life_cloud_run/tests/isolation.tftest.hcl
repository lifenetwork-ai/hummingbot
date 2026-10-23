# All tests use a mock provider, never GCP credentials or API calls.
mock_provider "google" {
  mock_resource "google_compute_network" {
    defaults = { id = "projects/offline-fixture/global/networks/life-synthetic", self_link = "https://www.googleapis.com/compute/v1/projects/offline-fixture/global/networks/life-synthetic" }
  }
  mock_resource "google_compute_subnetwork" {
    defaults = { id = "projects/offline-fixture/regions/asia-southeast1/subnetworks/life-synthetic" }
  }
  mock_resource "google_sql_database_instance" {
    defaults = {
      private_ip_address = "10.20.0.2"
      dns_names          = [{ name = "fixture.project.region.sql-psa.goog.", connection_type = "PRIVATE_SERVICES_ACCESS", dns_scope = "INSTANCE" }]
    }
  }
}
variables {
  project_id       = "offline-fixture"
  region           = "asia-southeast1"
  namespace        = "synthetic"
  generation       = "g1"
  image            = "asia-southeast1-docker.pkg.dev/offline-fixture/worker/life@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  worker_instances = 0
  network          = { subnet_cidr = "10.10.0.0/24", private_service_prefix_length = 16 }
  database         = { tier = "db-custom-2-7680", availability = "REGIONAL", disk_gb = 20, disk_limit_gb = 40, backups = 7, wal_days = 7 }
  resources        = { cpu = "2", memory = "2Gi", migration_timeout_seconds = 600 }
  runtime_dsn      = { secret = "fixture-runtime-g1", version = "1" }
  migration_dsn    = { secret = "fixture-migration", version = "1" }
  service_config   = { secret = "fixture-config", version = "1" }
  server_ca        = { secret = "fixture-ca", version = "1" }
  worker_env       = { LIFE_SOURCE_FACTORY = "trusted:factory", LIFE_TICK_SECONDS = "0.1", LIFE_STARTUP_TIMEOUT_SECONDS = "120", LIFE_SHUTDOWN_TIMEOUT_SECONDS = "8", LIFE_TELEMETRY_INTERVAL_SECONDS = "5" }
  # Synthetic offline thresholds; deployment requires operational calibration.
  monitoring = {
    notification_channels     = ["projects/offline-fixture/notificationChannels/123"]
    unknown_attempts          = 1, unresolved_holds = 64, budget_usage_ratio = 0.8
    event_loop_lag_ms         = 500, telemetry_max_age_ms = 15000, db_phase_ms = 1000
    heartbeat_absence_seconds = 120, notification_period_seconds = 600
  }
}
run "isolated_definition" {
  command = apply
  assert {
    condition = output.life_rollout_identity == {
      project          = var.project_id, region = var.region, migration_job = "life-synthetic-migrate",
      worker_instances = 0, migration_image = var.image, worker_image = var.image
    }
    error_message = "The release helper must receive the exact public stopped-worker and migration identity."
  }
  assert {
    condition     = google_cloud_run_v2_worker_pool.worker.scaling[0].manual_instance_count == 0 && google_cloud_run_v2_worker_pool.worker.scaling[0].scaling_mode == "MANUAL"
    error_message = "An offline profile must not allocate a worker or autoscale."
  }
  assert {
    condition     = google_cloud_run_v2_job.migration.template[0].task_count == 1 && google_cloud_run_v2_job.migration.template[0].parallelism == 1 && google_cloud_run_v2_job.migration.template[0].template[0].max_retries == 0
    error_message = "Migration must be serialized without platform retry."
  }
  assert {
    condition     = google_cloud_run_v2_worker_pool.worker.template[0].service_account != google_cloud_run_v2_job.migration.template[0].template[0].service_account && !contains([for row in google_secret_manager_secret_iam_member.worker : row.secret_id], var.migration_dsn.secret)
    error_message = "Runtime cannot share migration identity or secret access."
  }
  assert {
    condition     = google_cloud_run_v2_worker_pool.worker.template[0].vpc_access[0].egress == "ALL_TRAFFIC" && google_compute_router_nat.worker.nat_ip_allocate_option == "MANUAL_ONLY" && length(google_compute_router_nat.worker.nat_ips) == 1
    error_message = "Every worker SDK route must use one defined NAT egress."
  }
  assert {
    condition     = !google_sql_database_instance.primary.settings[0].ip_configuration[0].ipv4_enabled && google_sql_database_instance.primary.settings[0].ip_configuration[0].ssl_mode == "ENCRYPTED_ONLY" && google_sql_database_instance.primary.settings[0].backup_configuration[0].point_in_time_recovery_enabled
    error_message = "Database must remain private, encrypted and configured for PITR."
  }
  assert {
    condition     = one(google_dns_record_set.database.rrdatas) == "10.20.0.2" && google_dns_record_set.database.name == "fixture.project.region.sql-psa.goog."
    error_message = "Verify-full DNS must map the certified instance name to the private endpoint."
  }
}
run "one_worker_is_explicit" {
  command = plan
  variables { worker_instances = 1 }
  assert {
    condition     = google_cloud_run_v2_worker_pool.worker.scaling[0].manual_instance_count == 1
    error_message = "Exactly one desired process is required; this is not an exchange fence."
  }
}
run "reject_autoscale_count" {
  command = plan
  variables { worker_instances = 2 }
  expect_failures = [var.worker_instances]
}
run "reject_floating_image" {
  command = plan
  variables { image = "asia-docker.pkg.dev/fixture/worker:latest" }
  expect_failures = [var.image]
}
run "reject_floating_secret" {
  command = plan
  variables { runtime_dsn = { secret = "fixture-runtime-g1", version = "latest" } }
  expect_failures = [var.runtime_dsn]
}
run "reject_migration_secret_in_runtime" {
  command = plan
  variables { migration_dsn = { secret = "fixture-runtime-g1", version = "1" } }
  expect_failures = [var.migration_dsn]
}
run "reject_role_secret_in_ca_mount" {
  command = plan
  variables { server_ca = { secret = "fixture-migration", version = "1" } }
  expect_failures = [var.server_ca]
}
run "reject_runtime_migration_env_override" {
  command = plan
  variables { worker_env = { LIFE_MIGRATION_DSN = "never-put-credentials-in-tf" } }
  expect_failures = [var.worker_env]
}

run "generation_changes_revision_identity_not_pool_namespace" {
  command = plan
  variables { generation = "g2" }
  assert {
    condition     = google_cloud_run_v2_worker_pool.worker.name == "life-synthetic" && google_service_account.worker.account_id == "life-synthetic-g2-run" && google_cloud_run_v2_worker_pool.worker.template[0].labels["life_generation"] == "g2"
    error_message = "A new generation must use a distinct identity in the same stopped pool, not create a second protected pool."
  }
}

run "stopped_namespace_has_no_active_alerts" {
  command = plan
  assert {
    condition     = alltrue([for a in values(google_monitoring_alert_policy.worker) : !a.enabled]) && !google_monitoring_alert_policy.heartbeat.enabled
    error_message = "Intentional zero allocation must not create a missing-worker alert."
  }
  assert {
    condition     = google_monitoring_alert_policy.backup_failure.enabled
    error_message = "Backups must remain monitored while trading is intentionally stopped."
  }
  assert {
    condition     = strcontains(google_monitoring_alert_policy.worker["integrity"].conditions[0].condition_matched_log[0].filter, "jsonPayload.financial_metrics.recovery_health!=\"VERIFIED\"")
    error_message = "A fresh backup does not replace independent restore qualification."
  }
}
run "reject_unrouted_or_unbounded_monitoring" {
  command = plan
  variables {
    monitoring = {
      notification_channels     = [], unknown_attempts = 0, unresolved_holds = 0, budget_usage_ratio = 0
      event_loop_lag_ms         = 0, telemetry_max_age_ms = 0, db_phase_ms = 0
      heartbeat_absence_seconds = 0, notification_period_seconds = 0
    }
  }
  expect_failures = [var.monitoring]
}
