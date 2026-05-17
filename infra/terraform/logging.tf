# Cloud Logging + Monitoring scaffolding (#28).
#
# Cloud Run already streams stdout to Cloud Logging — the app emits
# structured JSON (see src/something_really_bot/logging.py) and Cloud
# Logging promotes the top-level `severity` field automatically. This
# file adds:
#
#   - A log-based counter metric for ERROR-or-worse log entries on this
#     service, used by the alert policy below.
#   - An email notification channel (when var.alerts_email is set).
#   - An alert policy that pages out the channel when the error count
#     exceeds the configured threshold over a rolling window.
#
# When `var.alerts_email` is empty the policy + channel are skipped, so
# applying this in a project without an operator email still succeeds.

locals {
  # Filter for the log-based metric: anything from the bot's Cloud Run
  # service whose severity is ERROR or worse. Stdout JSON with a
  # top-level "severity" field is what surfaces here — see
  # src/something_really_bot/logging.py::StructuredJsonFormatter.
  bot_error_log_filter = <<EOT
resource.type="cloud_run_revision"
resource.labels.service_name="${var.cloudrun_service_name}"
severity>=ERROR
EOT

  alerts_enabled = length(trimspace(var.alerts_email)) > 0
}

resource "google_logging_metric" "bot_errors" {
  project     = var.project_id
  name        = "${var.cloudrun_service_name}-errors"
  description = "Count of ERROR-or-worse log entries from the bot Cloud Run service (#28)."
  filter      = local.bot_error_log_filter

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    unit         = "1"
    display_name = "Bot Cloud Run error logs"
  }

  depends_on = [google_project_service.enabled]
}

resource "google_monitoring_notification_channel" "email" {
  count = local.alerts_enabled ? 1 : 0

  project      = var.project_id
  display_name = "Something Dashboard bot — operator email"
  type         = "email"

  labels = {
    email_address = var.alerts_email
  }

  depends_on = [google_project_service.enabled]
}

resource "google_monitoring_alert_policy" "bot_error_rate" {
  count = local.alerts_enabled ? 1 : 0

  project      = var.project_id
  display_name = "Something Dashboard bot — error rate"
  combiner     = "OR"
  enabled      = true

  conditions {
    display_name = "Bot Cloud Run ERROR-or-worse log entries exceed threshold"

    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.bot_errors.name}\" AND resource.type=\"cloud_run_revision\""
      duration        = "${var.alerts_error_window_seconds}s"
      comparison      = "COMPARISON_GT"
      threshold_value = var.alerts_error_threshold

      aggregations {
        alignment_period   = "${var.alerts_error_window_seconds}s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email[0].id]

  documentation {
    content   = "Bot logged more than ${var.alerts_error_threshold} ERROR-or-worse entries in the last ${var.alerts_error_window_seconds}s. Check Cloud Logging for stack traces, then inspect bot_responses / processing_events in BigQuery."
    mime_type = "text/markdown"
  }

  depends_on = [google_project_service.enabled]
}
