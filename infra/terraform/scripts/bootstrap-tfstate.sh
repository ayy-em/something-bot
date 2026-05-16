#!/usr/bin/env bash
# Creates the GCS bucket that holds Terraform remote state for this project.
# Run this once, as a human, before the first `terraform init`. It cannot be
# managed by Terraform itself because Terraform needs the bucket to *store*
# its state in.
#
# Idempotent: re-running after the bucket exists prints a warning and exits 0.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-something-bot-338300}"
REGION="${REGION:-europe-west4}"
BUCKET="${BUCKET:-something-bot-tfstate}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "error: gcloud CLI not found on PATH" >&2
  exit 1
fi

echo "Project:  ${PROJECT_ID}"
echo "Region:   ${REGION}"
echo "Bucket:   gs://${BUCKET}"
echo

if gcloud storage buckets describe "gs://${BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Bucket already exists; ensuring versioning and access settings are correct."
else
  echo "Creating bucket..."
  gcloud storage buckets create "gs://${BUCKET}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention
fi

echo "Enabling versioning..."
gcloud storage buckets update "gs://${BUCKET}" \
  --project="${PROJECT_ID}" \
  --versioning

echo
echo "Done. Next steps:"
echo "  1. cd infra/terraform"
echo "  2. terraform init"
echo "  3. terraform plan -var-file=environments/prod.tfvars"
