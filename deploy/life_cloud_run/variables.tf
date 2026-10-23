variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "namespace" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,11}$", var.namespace))
    error_message = "Use one short, explicit account namespace."
  }
}
variable "generation" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{0,7}$", var.generation))
    error_message = "Use a pinned generation ID, not an alias."
  }
}
variable "image" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9.-]+-docker.pkg.dev/[^ ]+@sha256:[0-9a-f]{64}$", var.image))
    error_message = "An immutable Artifact Registry image digest is required."
  }
}
variable "worker_instances" {
  type = number
  validation {
    condition     = contains([0, 1], var.worker_instances)
    error_message = "Select zero while preparing, or exactly one worker for this namespace."
  }
}
variable "network" {
  type = object({ subnet_cidr = string, private_service_prefix_length = number })
}
variable "database" {
  type = object({ tier = string, availability = string, disk_gb = number, disk_limit_gb = number, backups = number, wal_days = number })
  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.database.availability) && var.database.disk_gb >= 10 && var.database.disk_limit_gb >= var.database.disk_gb && var.database.backups >= 1 && var.database.wal_days >= 1 && var.database.wal_days <= 7
    error_message = "Explicit bounded disk, backup and PostgreSQL16 Enterprise PITR inputs are required."
  }
}
variable "resources" {
  type = object({ cpu = string, memory = string, migration_timeout_seconds = number })
  validation {
    condition     = var.resources.migration_timeout_seconds > 0 && var.resources.migration_timeout_seconds <= 3600 && contains(["1", "2", "4", "8"], var.resources.cpu)
    error_message = "Explicit bounded migration time and supported CPU resources are required."
  }
}
variable "worker_env" {
  type = map(string)
  validation {
    condition     = alltrue([for key in keys(var.worker_env) : !startswith(key, "LIFE_MIGRATION_") && !contains(["LIFE_AUTO_MIGRATE", "LIFE_ENV_FILE", "LIFE_PG_DSN", "LIFE_PG_LOCAL_INSECURE", "LIFE_PG_SSL_CA_FILE"], key)])
    error_message = "Worker settings cannot override secret, TLS, env-file or migration isolation."
  }
}
variable "runtime_dsn" {
  type = object({ secret = string, version = string })
  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.runtime_dsn.version))
    error_message = "Pin the runtime DSN secret to a numeric version."
  }
}
variable "migration_dsn" {
  type = object({ secret = string, version = string })
  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.migration_dsn.version)) && var.migration_dsn.secret != var.runtime_dsn.secret
    error_message = "Migration DSN needs a numeric version and a different secret from runtime."
  }
}
variable "server_ca" {
  type = object({ secret = string, version = string })
  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.server_ca.version)) && !contains([var.migration_dsn.secret, var.runtime_dsn.secret], var.server_ca.secret)
    error_message = "Pin the server CA certificate version."
  }
}
variable "service_config" {
  type = object({ secret = string, version = string })
  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.service_config.version)) && !contains([var.migration_dsn.secret, var.runtime_dsn.secret], var.service_config.secret)
    error_message = "Service configuration must be pinned separately from database credentials."
  }
}
