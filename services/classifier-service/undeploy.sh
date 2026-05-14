#!/bin/bash
# Usage:
#   bash undeploy.sh         — delete Cloud Run service (stops billing)
#   bash undeploy.sh --full  — delete everything (Cloud Run service + AR image)
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="europe-central2"
NAME="music-genres-classification"
FULL="${1:-}"

if [[ "${FULL}" != "" && "${FULL}" != "--full" ]]; then
  echo "Usage: bash undeploy.sh [--full]" >&2
  exit 1
fi

if [[ "${FULL}" == "--full" ]]; then
  : "${AR_REPO:?Set AR_REPO for --full}"
fi

echo "==> Deleting Cloud Run service ${NAME}..."
gcloud run services delete "${NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --quiet

if [[ "${FULL}" == "--full" ]]; then
  echo "==> Deleting AR image..."
  gcloud artifacts docker images delete \
    "${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${NAME}" \
    --project="${PROJECT_ID}" \
    --delete-tags \
    --quiet || echo "    Warning: could not delete AR image"

  echo ""
  echo "Done. Everything deleted."
else
  echo ""
  echo "Done. Cloud Run service deleted."
  echo "To redeploy: PROJECT_ID=${PROJECT_ID} AR_REPO=... bash deploy.sh"
fi
