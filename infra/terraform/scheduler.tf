# Cloud Scheduler infrastructure for scheduled bot jobs (#22).
#
# Adding a new job is a single locals entry — Terraform fans out one
# `google_cloud_scheduler_job` per map key. The actual job logic lives
# behind `POST /jobs/<name>` on Cloud Run; see
# `docs/architecture.md#scheduled-jobs`.

locals {
  # Per-job configuration.
  scheduled_jobs = {
    tiktok-reminder = {
      schedule    = "0 11 * * 5" # Friday 11:00
      timezone    = "Europe/Amsterdam"
      target_path = "/jobs/tiktok-reminder"
      description = "Friday TikTok reminder for Irindica (#24)."
    }
    daily-digest = {
      schedule    = "30 10 * * *" # Daily 10:30
      timezone    = "Europe/Amsterdam"
      target_path = "/jobs/daily-digest"
      description = "Daily website-stats digest + 24h job tally (#25, #54)."
    }
  }
}

# Service account Cloud Scheduler assumes when calling Cloud Run.
resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "something-bot-scheduler-sa"
  display_name = "Something Dashboard bot — Cloud Scheduler"
  description  = "OIDC identity Cloud Scheduler uses to invoke /jobs/* on Cloud Run."

  depends_on = [google_project_service.enabled]
}

# Scheduler SA must be able to invoke the Cloud Run service. The service
# itself is also `allUsers` invocable (the Telegram webhook), but the
# /jobs/* route relies on application-level OIDC verification using this
# SA's email as the trust anchor.
resource "google_cloud_run_v2_service_iam_member" "scheduler_invoker" {
  project  = google_cloud_run_v2_service.main.project
  location = google_cloud_run_v2_service.main.location
  name     = google_cloud_run_v2_service.main.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

# Cloud Scheduler also needs the cloudscheduler API enabled. Add it to
# the bootstrap apis list alongside the others. (Already toggled via
# locals.required_apis in main.tf — see project_service block.)

resource "google_cloud_scheduler_job" "jobs" {
  for_each = local.scheduled_jobs

  project     = var.project_id
  region      = var.region
  name        = "something-bot-${each.key}"
  description = each.value.description
  schedule    = each.value.schedule
  time_zone   = each.value.timezone

  attempt_deadline = "300s"

  retry_config {
    retry_count          = 1
    max_retry_duration   = "0s"
    min_backoff_duration = "5s"
    max_backoff_duration = "60s"
  }

  http_target {
    uri         = "${google_cloud_run_v2_service.main.uri}${each.value.target_path}"
    http_method = "POST"

    oidc_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [
    google_cloud_run_v2_service.main,
    google_cloud_run_v2_service_iam_member.scheduler_invoker,
    google_project_service.enabled,
  ]
}
