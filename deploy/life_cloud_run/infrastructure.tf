locals {
  prefix   = "life-${var.namespace}"
  services = toset(["run.googleapis.com", "sqladmin.googleapis.com", "compute.googleapis.com", "servicenetworking.googleapis.com", "artifactregistry.googleapis.com", "secretmanager.googleapis.com", "monitoring.googleapis.com", "logging.googleapis.com", "dns.googleapis.com", "iam.googleapis.com"])
}
resource "google_project_service" "api" {
  for_each           = local.services
  service            = each.key
  disable_on_destroy = false
}
resource "google_compute_network" "worker" {
  name                    = local.prefix
  auto_create_subnetworks = false
  depends_on              = [google_project_service.api]
}
resource "google_compute_subnetwork" "worker" {
  name                     = local.prefix
  region                   = var.region
  network                  = google_compute_network.worker.id
  ip_cidr_range            = var.network.subnet_cidr
  private_ip_google_access = true
}
resource "google_compute_global_address" "private_service" {
  name          = "${local.prefix}-sql-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = var.network.private_service_prefix_length
  network       = google_compute_network.worker.id
}
resource "google_service_networking_connection" "private_service" {
  network                 = google_compute_network.worker.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_service.name]
}
resource "google_compute_router" "worker" {
  name    = local.prefix
  network = google_compute_network.worker.id
  region  = var.region
}
resource "google_compute_address" "egress" {
  name   = "${local.prefix}-egress"
  region = var.region
}
resource "google_compute_router_nat" "worker" {
  name                               = local.prefix
  router                             = google_compute_router.worker.name
  region                             = var.region
  nat_ip_allocate_option             = "MANUAL_ONLY"
  nat_ips                            = [google_compute_address.egress.self_link]
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = google_compute_subnetwork.worker.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}
resource "google_sql_database_instance" "primary" {
  name                = "${local.prefix}-pg"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = true
  settings {
    tier                        = var.database.tier
    edition                     = "ENTERPRISE"
    availability_type           = var.database.availability
    disk_size                   = var.database.disk_gb
    disk_autoresize             = true
    disk_autoresize_limit       = var.database.disk_limit_gb
    deletion_protection_enabled = true
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.worker.id
      ssl_mode        = "ENCRYPTED_ONLY"
      server_ca_mode  = "GOOGLE_MANAGED_CAS_CA"
    }
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = var.database.wal_days
      backup_retention_settings {
        retained_backups = var.database.backups
        retention_unit   = "COUNT"
      }
    }
  }
  depends_on = [google_service_networking_connection.private_service]
}
resource "google_sql_database" "life" {
  name     = "life"
  instance = google_sql_database_instance.primary.name
}
resource "google_artifact_registry_repository" "worker" {
  location      = var.region
  repository_id = local.prefix
  format        = "DOCKER"
  depends_on    = [google_project_service.api]
}
resource "google_service_account" "worker" {
  account_id = "${local.prefix}-${var.generation}-run"
}
resource "google_service_account" "migration" {
  account_id = "${local.prefix}-migrate"
}
resource "google_project_iam_member" "logs" {
  project  = var.project_id
  for_each = { worker = google_service_account.worker.email, migration = google_service_account.migration.email }
  role     = "roles/logging.logWriter"
  member   = "serviceAccount:${each.value}"
}
resource "google_project_iam_member" "metrics" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.worker.email}"
}
# Secrets/versions are operator provisioned. Terraform never receives their
# values or writes database passwords into state. No worker actAs/token-creator
# or migration-job executor grant is installed.
resource "google_secret_manager_secret_iam_member" "worker" {
  for_each  = toset([var.runtime_dsn.secret, var.server_ca.secret, var.service_config.secret])
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}
resource "google_secret_manager_secret_iam_member" "migration" {
  for_each  = toset([var.migration_dsn.secret, var.server_ca.secret])
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.migration.email}"
}
locals {
  # CAS instance DNS is in the certificate SAN. No IP-only verification or
  # sslmode=disable proxy exception is added to the existing asyncpg policy.
  sql_hostname = one([for item in google_sql_database_instance.primary.dns_names : item.name if endswith(trimsuffix(item.name, "."), ".sql-psa.goog")])
}
resource "google_dns_managed_zone" "database" {
  name       = "${local.prefix}-database"
  dns_name   = "${trimsuffix(local.sql_hostname, ".")}."
  visibility = "private"
  private_visibility_config {
    networks { network_url = google_compute_network.worker.id }
  }
  depends_on = [google_project_service.api]
}
resource "google_dns_record_set" "database" {
  name         = google_dns_managed_zone.database.dns_name
  managed_zone = google_dns_managed_zone.database.name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_sql_database_instance.primary.private_ip_address]
}
output "verified_tls_hostname" { value = trimsuffix(local.sql_hostname, ".") }
