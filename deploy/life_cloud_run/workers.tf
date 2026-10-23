resource "google_cloud_run_v2_job" "migration" {
  name                = "${local.prefix}-migrate"
  location            = var.region
  deletion_protection = true
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.migration.email
      max_retries     = 0
      timeout         = "${var.resources.migration_timeout_seconds}s"
      vpc_access {
        egress = "ALL_TRAFFIC"
        network_interfaces {
          network    = google_compute_network.worker.name
          subnetwork = google_compute_subnetwork.worker.name
        }
      }
      volumes {
        name = "ca"
        secret {
          secret = var.server_ca.secret
          items {
            version = var.server_ca.version
            path    = "server-ca.pem"
          }
        }
      }
      containers {
        image   = var.image
        command = ["python", "-m", "hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations"]
        args    = ["up", "--no-env-file", "--require-tls"]
        resources {
          limits = { cpu = var.resources.cpu, memory = var.resources.memory }
        }
        env {
          name  = "LIFE_ENV_FILE"
          value = ""
        }
        env {
          name  = "LIFE_MIGRATION_SSL_CA_FILE"
          value = "/run/ca/server-ca.pem"
        }
        env {
          name  = "LIFE_MIGRATION_LOCAL_INSECURE"
          value = "false"
        }
        env {
          name = "LIFE_MIGRATION_DSN"
          value_source {
            secret_key_ref {
              secret  = var.migration_dsn.secret
              version = var.migration_dsn.version
            }
          }
        }
        volume_mounts {
          name       = "ca"
          mount_path = "/run/ca"
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.migration, google_compute_router_nat.worker]
}
resource "google_cloud_run_v2_worker_pool" "worker" {
  name                = local.prefix
  location            = var.region
  launch_stage        = "BETA"
  deletion_protection = true
  scaling {
    scaling_mode          = "MANUAL"
    manual_instance_count = var.worker_instances
  }
  template {
    labels          = { life_generation = var.generation }
    service_account = google_service_account.worker.email
    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = google_compute_network.worker.name
        subnetwork = google_compute_subnetwork.worker.name
        tags       = ["${local.prefix}-worker"]
      }
    }
    volumes {
      name = "ca"
      secret {
        secret = var.server_ca.secret
        items {
          version = var.server_ca.version
          path    = "server-ca.pem"
        }
      }
    }
    volumes {
      name = "config"
      secret {
        secret = var.service_config.secret
        items {
          version = var.service_config.version
          path    = "service.yml"
        }
      }
    }
    containers {
      image   = var.image
      command = ["python", "-m", "hummingbot.strategy_v2.life_liquidity.storage.postgres.worker"]
      resources {
        limits = { cpu = var.resources.cpu, memory = var.resources.memory }
      }
      dynamic "env" {
        for_each = merge(var.worker_env, { LIFE_AUTO_MIGRATE = "false", LIFE_ENV_FILE = "", LIFE_PG_LOCAL_INSECURE = "false", LIFE_PG_SSL_CA_FILE = "/run/ca/server-ca.pem", LIFE_SERVICE_CONFIG = "/run/config/service.yml", LIFE_STATUS_HOST = "0.0.0.0", LIFE_STATUS_PORT = "8080" })
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name = "LIFE_PG_DSN"
        value_source {
          secret_key_ref {
            secret  = var.runtime_dsn.secret
            version = var.runtime_dsn.version
          }
        }
      }
      volume_mounts {
        name       = "ca"
        mount_path = "/run/ca"
      }
      volume_mounts {
        name       = "config"
        mount_path = "/run/config"
      }
      startup_probe {
        http_get {
          path = "/startup"
          port = 8080
        }
        period_seconds    = 10
        timeout_seconds   = 1
        failure_threshold = 24
      }
      liveness_probe {
        http_get {
          path = "/live"
          port = 8080
        }
        period_seconds    = 10
        timeout_seconds   = 1
        failure_threshold = 3
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.worker, google_compute_router_nat.worker]
}
output "migration_job" { value = google_cloud_run_v2_job.migration.name }
output "worker_pool" { value = google_cloud_run_v2_worker_pool.worker.name }
output "egress_ip" { value = google_compute_address.egress.address }
output "database_private_ip" { value = google_sql_database_instance.primary.private_ip_address }
output "database_dns_names" { value = google_sql_database_instance.primary.dns_names }
