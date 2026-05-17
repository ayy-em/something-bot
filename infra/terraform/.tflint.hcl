// tflint configuration for the bot's Terraform (#29).
//
// Pulls in the Google ruleset for provider-aware checks (deprecated
// resource attrs, invalid region/zone values, etc.) on top of the
// built-in core rules.

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "google" {
  enabled = true
  version = "0.30.0"
  source  = "github.com/terraform-linters/tflint-ruleset-google"
}
