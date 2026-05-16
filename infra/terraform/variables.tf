variable "project_id" {
  description = "GCP project hosting the bot."
  type        = string
}

variable "region" {
  description = "GCP region for regional resources (Cloud Run, Artifact Registry, GCS bucket)."
  type        = string
  default     = "europe-west4"
}

variable "github_repo" {
  description = "GitHub repository (owner/name) authorised to assume the deployer service account via Workload Identity Federation."
  type        = string
}

variable "artifact_repo_name" {
  description = "Artifact Registry Docker repository name."
  type        = string
  default     = "something-really-bot-artifacts"
}

variable "cloudrun_service_name" {
  description = "Cloud Run service name."
  type        = string
  default     = "something-really-bot-cloudrun"
}

variable "cloudrun_image_placeholder" {
  description = "Bootstrap image used at first terraform apply. CI in #9 replaces this on every deploy via gcloud; the image attribute is in the lifecycle ignore_changes list so Terraform never reverts it."
  type        = string
  default     = "gcr.io/cloudrun/hello"
}

variable "telegram_files_bucket_name" {
  description = "GCS bucket Cloud Run writes Telegram-uploaded files to (#20)."
  type        = string
  default     = "something-bot-telegram-files"
}

variable "cloudrun_settings" {
  description = "Cloud Run runtime knobs. Defaults encode the Cloud Run Settings RFC for SPEC §18.3."
  type = object({
    cpu             = string
    memory          = string
    timeout_seconds = number
    concurrency     = number
    min_instances   = number
    max_instances   = number
  })
  default = {
    cpu             = "1"
    memory          = "512Mi"
    timeout_seconds = 60
    concurrency     = 80
    min_instances   = 0
    max_instances   = 3
  }
}

variable "bots" {
  description = "Per-bot configuration. Each entry references an existing Secret Manager secret holding the bot token plus a QA-users allowlist secret; a webhook-secret placeholder is provisioned per bot. Add bots without refactoring by appending entries."
  type = map(object({
    telegram_bot_token_secret_name = string
    telegram_qa_users_secret_name  = string
  }))
  default = {
    default = {
      telegram_bot_token_secret_name = "telegram-bot-token-default"
      telegram_qa_users_secret_name  = "telegram-qa-users"
    }
  }
}

variable "openai_api_key_secret_name" {
  description = "Existing Secret Manager secret holding the OpenAI API key. Upper-snake-cased intentionally to match the legacy app.yaml env var name."
  type        = string
  default     = "OPENAI_API_KEY"
}
