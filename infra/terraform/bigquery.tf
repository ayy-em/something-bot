# BigQuery dataset + tables for Telegram bot persistence.
#
# Schema source of truth: docs/decisions/0001-bigquery-schema.md (RFC #17).
# Implementation issue: #18. Keep this file and the decision record in sync;
# additive changes only (new nullable columns), never rename or drop in place.

locals {
  bigquery_table_schemas = {
    telegram_updates_raw = jsonencode([
      { name = "update_id", type = "INT64", mode = "REQUIRED" },
      { name = "bot_id", type = "STRING", mode = "REQUIRED" },
      { name = "update_type", type = "STRING", mode = "REQUIRED" },
      { name = "raw_payload", type = "JSON", mode = "REQUIRED" },
      { name = "received_at", type = "TIMESTAMP", mode = "REQUIRED" },
    ])

    telegram_messages = jsonencode([
      { name = "update_id", type = "INT64", mode = "REQUIRED" },
      { name = "bot_id", type = "STRING", mode = "REQUIRED" },
      { name = "message_id", type = "INT64", mode = "REQUIRED" },
      { name = "chat_id", type = "INT64", mode = "REQUIRED" },
      { name = "chat_type", type = "STRING", mode = "REQUIRED" },
      { name = "chat_title", type = "STRING", mode = "NULLABLE" },
      { name = "user_id", type = "INT64", mode = "NULLABLE" },
      { name = "username", type = "STRING", mode = "NULLABLE" },
      { name = "message_type", type = "STRING", mode = "REQUIRED" },
      { name = "command", type = "STRING", mode = "NULLABLE" },
      { name = "text", type = "STRING", mode = "NULLABLE" },
      { name = "received_at", type = "TIMESTAMP", mode = "REQUIRED" },
      { name = "processed_at", type = "TIMESTAMP", mode = "NULLABLE" },
      { name = "processing_status", type = "STRING", mode = "REQUIRED" },
      { name = "handler_name", type = "STRING", mode = "NULLABLE" },
      { name = "error", type = "STRING", mode = "NULLABLE" },
    ])

    telegram_files = jsonencode([
      { name = "update_id", type = "INT64", mode = "REQUIRED" },
      { name = "bot_id", type = "STRING", mode = "REQUIRED" },
      { name = "chat_id", type = "INT64", mode = "REQUIRED" },
      { name = "message_id", type = "INT64", mode = "REQUIRED" },
      { name = "file_id", type = "STRING", mode = "REQUIRED" },
      { name = "file_unique_id", type = "STRING", mode = "REQUIRED" },
      { name = "file_type", type = "STRING", mode = "REQUIRED" },
      { name = "mime_type", type = "STRING", mode = "NULLABLE" },
      { name = "file_size_bytes", type = "INT64", mode = "NULLABLE" },
      { name = "original_filename", type = "STRING", mode = "NULLABLE" },
      { name = "gcs_uri", type = "STRING", mode = "NULLABLE" },
      { name = "download_status", type = "STRING", mode = "REQUIRED" },
      { name = "received_at", type = "TIMESTAMP", mode = "REQUIRED" },
      { name = "downloaded_at", type = "TIMESTAMP", mode = "NULLABLE" },
      { name = "error", type = "STRING", mode = "NULLABLE" },
    ])

    bot_responses = jsonencode([
      { name = "bot_id", type = "STRING", mode = "REQUIRED" },
      { name = "in_response_to_update_id", type = "INT64", mode = "NULLABLE" },
      { name = "chat_id", type = "INT64", mode = "REQUIRED" },
      { name = "message_id", type = "INT64", mode = "NULLABLE" },
      { name = "response_type", type = "STRING", mode = "REQUIRED" },
      { name = "text", type = "STRING", mode = "NULLABLE" },
      { name = "sent_at", type = "TIMESTAMP", mode = "REQUIRED" },
      { name = "success", type = "BOOL", mode = "REQUIRED" },
      { name = "error", type = "STRING", mode = "NULLABLE" },
    ])

    processing_events = jsonencode([
      { name = "update_id", type = "INT64", mode = "NULLABLE" },
      { name = "bot_id", type = "STRING", mode = "REQUIRED" },
      { name = "event", type = "STRING", mode = "REQUIRED" },
      { name = "handler_name", type = "STRING", mode = "NULLABLE" },
      { name = "status", type = "STRING", mode = "REQUIRED" },
      { name = "details", type = "STRING", mode = "NULLABLE" },
      { name = "occurred_at", type = "TIMESTAMP", mode = "REQUIRED" },
    ])
  }

  bigquery_table_partitioning = {
    telegram_updates_raw = "received_at"
    telegram_messages    = "received_at"
    telegram_files       = "received_at"
    bot_responses        = "sent_at"
    processing_events    = "occurred_at"
  }

  bigquery_table_clustering = {
    telegram_updates_raw = ["bot_id", "update_type"]
    telegram_messages    = ["bot_id", "chat_type", "message_type"]
    telegram_files       = ["bot_id", "file_type"]
    bot_responses        = ["bot_id", "chat_id"]
    processing_events    = ["bot_id", "status"]
  }
}

resource "google_bigquery_dataset" "something_bot" {
  project                    = var.project_id
  dataset_id                 = var.bigquery_dataset_id
  location                   = var.bigquery_location
  description                = "Telegram bot persistence (raw updates, messages, files, responses, events). Schema: docs/decisions/0001-bigquery-schema.md."
  delete_contents_on_destroy = false

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table" "tables" {
  for_each = local.bigquery_table_schemas

  project    = var.project_id
  dataset_id = google_bigquery_dataset.something_bot.dataset_id
  table_id   = each.key
  schema     = each.value

  time_partitioning {
    type  = "DAY"
    field = local.bigquery_table_partitioning[each.key]
  }

  clustering = local.bigquery_table_clustering[each.key]

  deletion_protection = true
}

resource "google_bigquery_dataset_iam_member" "cloudrun_data_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.something_bot.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.cloudrun.email}"
}
