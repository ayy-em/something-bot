locals {
  required_apis = [
    "artifactregistry.googleapis.com",
    "cloudbilling.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ]

  deployer_project_roles = [
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
    "roles/run.admin",
  ]

  webhook_secret_ids = {
    for key, _ in var.bots : key => "telegram-webhook-secret-${key}"
  }
}

# --------------------------------------------------------------------------- #
# Project APIs
# --------------------------------------------------------------------------- #

resource "google_project_service" "enabled" {
  for_each = toset(local.required_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --------------------------------------------------------------------------- #
# Artifact Registry (Docker)
# --------------------------------------------------------------------------- #

resource "google_artifact_registry_repository" "docker" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_repo_name
  description   = "Docker images for the Something Dashboard Telegram bot."
  format        = "DOCKER"

  depends_on = [google_project_service.enabled]
}

# --------------------------------------------------------------------------- #
# GCS bucket for Telegram-uploaded files
# --------------------------------------------------------------------------- #

resource "google_storage_bucket" "telegram_files" {
  project                     = var.project_id
  name                        = var.telegram_files_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
  public_access_prevention    = "enforced"

  depends_on = [google_project_service.enabled]
}

# --------------------------------------------------------------------------- #
# Service accounts
# --------------------------------------------------------------------------- #

resource "google_service_account" "cloudrun" {
  project      = var.project_id
  account_id   = "something-bot-cloudrun-sa"
  display_name = "Something Dashboard bot — Cloud Run runtime"
  description  = "Runtime identity for the Cloud Run service. Reads secrets and writes to BigQuery / GCS."

  depends_on = [google_project_service.enabled]
}

resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "something-bot-deployer-sa"
  display_name = "Something Dashboard bot — GitHub Actions deployer"
  description  = "Assumed by GitHub Actions via Workload Identity Federation to push images and deploy Cloud Run."

  depends_on = [google_project_service.enabled]
}

# --------------------------------------------------------------------------- #
# Workload Identity Federation for GitHub Actions
# --------------------------------------------------------------------------- #

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions pool"
  description               = "OIDC pool for GitHub Actions deployments of something-bot."

  depends_on = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub Actions OIDC"

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
    "attribute.ref"              = "assertion.ref"
  }

  attribute_condition = "assertion.repository == \"${var.github_repo}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

resource "google_project_iam_member" "deployer" {
  for_each = toset(local.deployer_project_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# --------------------------------------------------------------------------- #
# Secret Manager — existing secrets referenced as data sources
# --------------------------------------------------------------------------- #

data "google_secret_manager_secret" "telegram_bot_token" {
  for_each = var.bots

  project   = var.project_id
  secret_id = each.value.telegram_bot_token_secret_name
}

data "google_secret_manager_secret" "telegram_qa_users" {
  for_each = var.bots

  project   = var.project_id
  secret_id = each.value.telegram_qa_users_secret_name
}

data "google_secret_manager_secret" "openai_api_key" {
  project   = var.project_id
  secret_id = var.openai_api_key_secret_name
}

# --------------------------------------------------------------------------- #
# Secret Manager — webhook secret placeholder per bot (value injected out-of-band)
# --------------------------------------------------------------------------- #

resource "google_secret_manager_secret" "telegram_webhook_secret" {
  for_each = var.bots

  project   = var.project_id
  secret_id = local.webhook_secret_ids[each.key]

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

# --------------------------------------------------------------------------- #
# Secret access for the Cloud Run runtime service account
# --------------------------------------------------------------------------- #

resource "google_secret_manager_secret_iam_member" "cloudrun_bot_token" {
  for_each = var.bots

  project   = var.project_id
  secret_id = data.google_secret_manager_secret.telegram_bot_token[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloudrun.email}"
}

resource "google_secret_manager_secret_iam_member" "cloudrun_qa_users" {
  for_each = var.bots

  project   = var.project_id
  secret_id = data.google_secret_manager_secret.telegram_qa_users[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloudrun.email}"
}

resource "google_secret_manager_secret_iam_member" "cloudrun_webhook_secret" {
  for_each = var.bots

  project   = var.project_id
  secret_id = google_secret_manager_secret.telegram_webhook_secret[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloudrun.email}"
}

resource "google_secret_manager_secret_iam_member" "cloudrun_openai" {
  project   = var.project_id
  secret_id = data.google_secret_manager_secret.openai_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloudrun.email}"
}

# --------------------------------------------------------------------------- #
# GCS bucket access for the Cloud Run runtime service account
# --------------------------------------------------------------------------- #

resource "google_storage_bucket_iam_member" "cloudrun_files" {
  bucket = google_storage_bucket.telegram_files.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.cloudrun.email}"
}

# --------------------------------------------------------------------------- #
# Cloud Run service (placeholder image; CI replaces it)
# --------------------------------------------------------------------------- #

resource "google_cloud_run_v2_service" "main" {
  project  = var.project_id
  name     = var.cloudrun_service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account                  = google_service_account.cloudrun.email
    timeout                          = "${var.cloudrun_settings.timeout_seconds}s"
    max_instance_request_concurrency = var.cloudrun_settings.concurrency

    scaling {
      min_instance_count = var.cloudrun_settings.min_instances
      max_instance_count = var.cloudrun_settings.max_instances
    }

    containers {
      image = var.cloudrun_image_placeholder

      resources {
        limits = {
          cpu    = var.cloudrun_settings.cpu
          memory = var.cloudrun_settings.memory
        }
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  lifecycle {
    # Image and env vars are owned by the deploy workflow (gcloud run deploy
    # --image / --set-secrets in .github/workflows/deploy.yml). Without these
    # ignores, every `terraform apply` would strip the secret env vars the
    # workflow injects (TELEGRAM_WEBHOOK_SECRET, TELEGRAM_BOT_TOKEN,
    # TELEGRAM_QA_USERS) and the next revision would crash on boot.
    ignore_changes = [
      template[0].containers[0].image,
      template[0].containers[0].env,
    ]
  }

  depends_on = [
    google_project_service.enabled,
    google_secret_manager_secret_iam_member.cloudrun_bot_token,
    google_secret_manager_secret_iam_member.cloudrun_qa_users,
    google_secret_manager_secret_iam_member.cloudrun_webhook_secret,
    google_secret_manager_secret_iam_member.cloudrun_openai,
    google_storage_bucket_iam_member.cloudrun_files,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = google_cloud_run_v2_service.main.project
  location = google_cloud_run_v2_service.main.location
  name     = google_cloud_run_v2_service.main.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
