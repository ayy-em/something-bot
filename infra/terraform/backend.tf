terraform {
  backend "gcs" {
    bucket = "something-bot-tfstate"
    prefix = "something-bot/prod"
  }
}
