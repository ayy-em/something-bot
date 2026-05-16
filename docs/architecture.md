# Architecture

> Stub. Fleshed out incrementally as features land. The authoritative target
> state lives in [`SPEC.md`](../SPEC.md); this document describes what is
> *implemented* in the repo right now.

## Current state

What exists in the repo today:

- FastAPI shell at `src/something_really_bot/main.py` exposing `GET /healthz`
  (returns `{"status": "healthy"}`) and `POST /webhook` (hello-world; returns
  `{"status": "ok"}` for any payload, no validation yet).
- Empty package stubs for the layers below.
- A handful of smoke tests, Dockerfile, README, ruff/pytest config.
- Full Terraform foundation under `infra/terraform/` for the GCP side.
- GitHub Actions CI + OIDC-authenticated deploy workflow under
  `.github/workflows/`.

No Telegram secret-header validation, parsing, routing, persistence, or
business logic yet. The legacy Python 3.9 / Flask / App Engine code has
been removed (#11) — anything that was in `channel/`, `fc/`, `reminders/`,
`stuff_for_ira/`, `utils/`, or `main.py` is in git history if needed for
migration reference.

## Layer boundaries (target)

The package tree under `src/something_really_bot/` reserves these boundaries
ahead of implementation. Each layer is replaced as its issue lands.

| Layer | Module | Issue introducing real logic |
| --- | --- | --- |
| Web / API | `main.py` | #10 (hello-world), #12 (secret header) |
| Telegram client + parser | `telegram/` | #12, #13 |
| Routing / dispatcher | `routing/` | #14 |
| Feature handlers | `features/` | #15, #16, #20, #23 … |
| BigQuery persistence | `persistence/` | #17 (RFC), #18 |
| GCS file storage | `file_storage/` | #20 |
| Cross-feature services | `services/` | as needed |
| Config / secrets | `config.py` | #12 onward |
| Logging | `logging.py` | TBD |

## Infrastructure

Terraform-managed Cloud Run deployment lands in #8. CI/CD via GitHub Actions
OIDC lands in #9. Until then, the Dockerfile alone proves the image builds.

## Conventions

- Python 3.12, pinned via `.python-version` and `requires-python` in
  `pyproject.toml`.
- Dependency management: `uv` (lockfile committed).
- Lint / format: `ruff` (configured in `pyproject.toml`).
- Tests: `pytest`. External services (Telegram, BigQuery, GCS, Secret Manager)
  must be mocked.
- Enforcement: CI workflow gates merges on `ruff format --check`,
  `ruff check`, and `pytest`. Pre-commit hooks are intentionally not used.
