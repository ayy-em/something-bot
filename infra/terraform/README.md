# Terraform — Something Dashboard bot infrastructure

This directory defines all GCP resources the bot needs in project
`something-bot-338300` (region `europe-west4`):

- Cloud Run service `something-really-bot-cloudrun` (bootstrap image only;
  CI in #9 replaces it on every deploy and the image attribute is in the
  Terraform `lifecycle.ignore_changes` list).
- Artifact Registry Docker repo `something-really-bot-artifacts`.
- GCS bucket `something-bot-telegram-files` for received Telegram files.
- Two service accounts: `something-bot-cloudrun-sa` (runtime) and
  `something-bot-deployer-sa` (assumed by GitHub Actions via WIF).
- Workload Identity Federation pool + provider bound to
  `github.com/ayy-em/something-bot`.
- Secret Manager: data references to four existing secrets plus a new
  webhook-secret placeholder per bot.
- IAM bindings for the SAs above (least privilege).

BigQuery and Cloud Scheduler resources are intentionally not here — they
arrive in #18 and #22 respectively.

## Layout

```
infra/terraform/
├── versions.tf            ← Terraform + provider version pins
├── providers.tf           ← google provider
├── backend.tf             ← gcs remote backend
├── variables.tf           ← input variables
├── main.tf                ← all resources
├── outputs.tf             ← outputs (Cloud Run URL, SA emails, WIF provider, …)
├── environments/
│   └── prod.tfvars        ← prod variable values
└── scripts/
    └── bootstrap-tfstate.sh ← one-off: create state bucket
```

## One-time bootstrap (do this once before first `terraform init`)

1. **Authenticate gcloud against the target project:**

   ```bash
   gcloud auth login
   gcloud auth application-default login
   gcloud config set project something-bot-338300
   ```

2. **Create the Terraform state bucket** (Terraform can't manage its own
   state bucket — chicken-and-egg):

   ```bash
   ./scripts/bootstrap-tfstate.sh
   ```

   Override defaults via env vars if needed:
   `PROJECT_ID=... REGION=... BUCKET=... ./scripts/bootstrap-tfstate.sh`

3. **Ensure the four referenced secrets exist** in Secret Manager.
   Terraform reads three of them as `data` sources; if any are missing, the
   plan fails with `Error: Secret … not found`. Create empty placeholders
   for whichever don't exist yet:

   ```bash
   for s in telegram-bot-token-default telegram-qa-users OPENAI_API_KEY; do
     gcloud secrets describe "$s" --project=something-bot-338300 >/dev/null 2>&1 \
       || gcloud secrets create "$s" --project=something-bot-338300 \
            --replication-policy=automatic
   done
   ```

   The webhook secret (`telegram-webhook-secret-default`) is created by
   Terraform itself; only its *value* must be added out-of-band (see
   `Populating secret values` below).

## Plan / apply

```bash
cd infra/terraform
terraform init
terraform plan -var-file=environments/prod.tfvars
terraform apply -var-file=environments/prod.tfvars
```

A successful apply prints (among others) the Cloud Run URL, deployer SA
email, and the WIF provider resource name — feed the latter two into the
GitHub Actions workflow in #9.

## Populating secret values

Terraform never touches secret values. After apply, populate the webhook
secret (random string of your choice — Telegram echoes it as the
`X-Telegram-Bot-Api-Secret-Token` header):

```bash
echo -n "<random-secret-string>" \
  | gcloud secrets versions add telegram-webhook-secret-default \
      --project=something-bot-338300 --data-file=-
```

Do the same for `telegram-bot-token-default`, `telegram-qa-users`, and
`OPENAI_API_KEY` if their values are not already set.

## CI gates (#29)

The repository's GitHub Actions `_checks.yml` workflow runs five gates on
every PR that touches anything (and as a prerequisite of the `deploy`
job on pushes to `master`):

1. `terraform fmt -check -recursive`
2. `terraform init -backend=false -input=false`
3. `terraform validate`
4. `tflint --recursive` (with the `google` provider ruleset — see
   `.tflint.hcl`)
5. **Real `terraform plan`** against `something-bot-338300` via WIF
   (planner SA, strictly read-only). Plan output is posted as a PR
   comment and to the workflow `$GITHUB_STEP_SUMMARY`.

Gate (5) is gated on `GCP_PLANNER_SERVICE_ACCOUNT` being set as a repo
secret; without it the plan job is skipped (a notice surfaces in the
run-list), but gates 1–4 still run.

To enable plan-on-PR after applying:

```bash
terraform output -raw planner_service_account_email
# put this value into GitHub → Settings → Secrets → Actions →
#   GCP_PLANNER_SERVICE_ACCOUNT
```

`GCP_WORKLOAD_IDENTITY_PROVIDER` is reused (same provider as the
deployer).

### Deploy gating

`deploy.yml` chains `deploy → preflight → checks`; if any gate above
fails on `master`, the deploy job is skipped and the workflow exits
non-zero so it's visible at the run-list level. Failing-loud is
preferable to silent infra drift.

### Running locally

The same commands run cleanly locally with `gcloud auth
application-default login` against the project:

```bash
terraform fmt -check -recursive
terraform init -backend=false -input=false
terraform validate
tflint --init && tflint --recursive --format=compact
terraform init                          # real backend
terraform plan -var-file=environments/prod.tfvars
```

## Cloud Run settings (RFC for SPEC §18.3)

| Setting | Value | Rationale |
| --- | --- | --- |
| Region | `europe-west4` | SPEC §8 mandate; EU data residency. |
| CPU | 1 vCPU | Telegram updates are tiny; one vCPU well under-utilized. |
| Memory | 512 MiB | Generous for FastAPI + future BigQuery/GCS clients. |
| Timeout | 60 s | Webhook responses are sub-second; 60 s leaves room for file-fetch edge cases (#20). |
| Concurrency | 80 | Default. Webhook traffic is bursty-but-tiny. |
| Min instances | 0 | Cold starts acceptable for the expected volume. |
| Max instances | 3 | Bounds cost. |
| Ingress | `INGRESS_TRAFFIC_ALL` | Telegram delivers webhooks from the public internet. |
| Auth | Allow unauthenticated (allUsers `run.invoker`) + secret-header check inside the app | Required by Telegram; the header validation in #12 enforces real auth. |
| Runtime SA | `something-bot-cloudrun-sa` | Least-privilege. |

All knobs are tweakable via the `cloudrun_settings` variable.

## Multi-bot

The `bots` map variable parametrizes the per-bot Secret Manager resources.
Adding a second bot is a `prod.tfvars` change:

```hcl
bots = {
  default = {
    telegram_bot_token_secret_name = "telegram-bot-token-default"
    telegram_qa_users_secret_name  = "telegram-qa-users"
  }
  another_one = {
    telegram_bot_token_secret_name = "telegram-bot-token-another_one"
    telegram_qa_users_secret_name  = "telegram-qa-users-another_one"
  }
}
```

A separate webhook-secret placeholder, three IAM bindings, and a Secret
Manager resource will be planned for the new bot automatically.

## Out of scope (covered elsewhere)

- BigQuery dataset / tables — #18.
- Cloud Scheduler endpoints — #22.
- CI deployment workflow — #9.
- Cloud Logging sinks / alerting — #28.
