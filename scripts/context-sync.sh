#!/usr/bin/env bash
# Sync the persistent OpenAI context (#26) between this developer's
# machine and the GCS bucket the Cloud Run runtime reads from.
#
# Usage:
#   scripts/context-sync.sh push   # local-context/  →  gs://<bucket>/context/
#   scripts/context-sync.sh pull   # gs://<bucket>/context/ → local-context/
#
# Bucket name is read from the OPENAI_CONTEXT_BUCKET env var, falling
# back to the value pinned in scripts/.openai-context-bucket. The
# bucket name itself is intentionally repo-tracked; only the .md
# content is gitignored.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DIR="${REPO_ROOT}/local-context"
BUCKET_FILE="${REPO_ROOT}/scripts/.openai-context-bucket"

resolve_bucket() {
    if [[ -n "${OPENAI_CONTEXT_BUCKET:-}" ]]; then
        echo "${OPENAI_CONTEXT_BUCKET}"
        return
    fi
    if [[ -f "${BUCKET_FILE}" ]]; then
        head -n 1 "${BUCKET_FILE}"
        return
    fi
    echo "ERROR: bucket name not set (export OPENAI_CONTEXT_BUCKET or populate ${BUCKET_FILE})." >&2
    exit 2
}

direction="${1:-}"
bucket="$(resolve_bucket)"
remote="gs://${bucket}/context/"

mkdir -p "${LOCAL_DIR}"

case "${direction}" in
    push)
        echo "Pushing ${LOCAL_DIR}/  →  ${remote}"
        gcloud storage rsync "${LOCAL_DIR}/" "${remote}" --delete-unmatched-destination-objects
        ;;
    pull)
        echo "Pulling ${remote}  →  ${LOCAL_DIR}/"
        gcloud storage rsync "${remote}" "${LOCAL_DIR}/" --delete-unmatched-destination-objects
        ;;
    *)
        echo "Usage: $0 {push|pull}" >&2
        exit 2
        ;;
esac
