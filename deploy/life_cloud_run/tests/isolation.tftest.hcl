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
}
run "isolated_definition" {
  command = apply
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
