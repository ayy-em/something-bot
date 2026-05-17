output "cloud_run_url" {
  description = "Public URL of the Cloud Run service. Set this (with /webhook appended) as the Telegram webhook target."
  value       = google_cloud_run_v2_service.main.uri
}

output "cloud_run_service_account_email" {
  description = "Runtime service account used by the Cloud Run service."
  value       = google_service_account.cloudrun.email
}

output "deployer_service_account_email" {
  description = "Service account assumed by GitHub Actions for deploys. Goes into the workflow as `service_account` for the `google-github-actions/auth` step."
  value       = google_service_account.deployer.email
}

output "workload_identity_provider" {
  description = "Fully-qualified WIF provider resource name. Goes into the workflow as `workload_identity_provider` for the `google-github-actions/auth` step."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "artifact_registry_repository" {
  description = "Artifact Registry Docker repository path used to tag and push images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}"
}

output "telegram_files_bucket" {
  description = "GCS bucket Cloud Run writes Telegram-uploaded files to."
  value       = google_storage_bucket.telegram_files.name
}

output "openai_context_bucket" {
  description = "GCS bucket holding the persistent OpenAI context .md files (#26). scripts/context-sync.sh syncs to/from this name."
  value       = google_storage_bucket.openai_context.name
}

output "webhook_secret_names" {
  description = "Per-bot Secret Manager secret names holding the Telegram webhook header secret. Values must be populated out-of-band before first deploy."
  value       = { for k, s in google_secret_manager_secret.telegram_webhook_secret : k => s.secret_id }
}
