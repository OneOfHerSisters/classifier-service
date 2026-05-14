#!/bin/bash
# Usage: PROJECT_ID=my-project AR_REPO=my-repo [IMAGE_TAG=v1] bash deploy.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
AR_REPO="${AR_REPO:?Set AR_REPO (Artifact Registry repository name)}"
REGION="europe-central2"
AR_HOST="${REGION}-docker.pkg.dev"
IMAGE="${AR_HOST}/${PROJECT_ID}/${AR_REPO}/music-genres-classification:${IMAGE_TAG:-latest}"
NAME="music-genres-classification"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

LOCK="/tmp/deploy-${NAME}.lock"
if [ -f "${LOCK}" ]; then
  echo "Error: another deploy is already running (${LOCK} exists)" >&2
  exit 1
fi
touch "${LOCK}"
trap "rm -f ${LOCK}" EXIT

# Fail fast if gcloud is not authenticated
if ! gcloud auth print-access-token &>/dev/null; then
  echo "Error: not authenticated. Run: gcloud auth login" >&2
  exit 1
fi

echo "==> Verifying Artifact Registry repository..."
gcloud artifacts repositories describe "${AR_REPO}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" >/dev/null

echo "==> Building image via Cloud Build: ${IMAGE}"
BUILD_ID=$(gcloud builds submit \
  --config="${SCRIPT_DIR}/cloudbuild.yaml" \
  --substitutions="_AR_REPO=${AR_REPO},COMMIT_SHA=${IMAGE_TAG:-latest}" \
  --project="${PROJECT_ID}" \
  --async \
  --format="value(id)" \
  "${REPO_ROOT}")
echo "    Build ID: ${BUILD_ID}"
echo "    Waiting for build..."
while true; do
  STATUS=$(gcloud builds describe "${BUILD_ID}" \
    --project="${PROJECT_ID}" \
    --format="value(status)" 2>/dev/null || echo "UNKNOWN")
  echo "    Status: ${STATUS}"
  case "${STATUS}" in
    SUCCESS) break ;;
    FAILURE|TIMEOUT|CANCELLED|INTERNAL_ERROR) echo "Build failed: ${STATUS}" >&2; exit 1 ;;
    UNKNOWN|"") echo "Unable to read build status" >&2; exit 1 ;;
  esac
  sleep 15
done

echo "==> Deploying to Cloud Run..."
gcloud run deploy "${NAME}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --port=8080 \
  --memory=8Gi \
  --cpu=2 \
  --timeout=300 \
  --min-instances=0 \
  --max-instances=3 \
  --set-env-vars="SONGS_COLLECTION=audio_metadata_dev,SCORES_COLLECTION=audio_genre_scores_dev" \
  --no-allow-unauthenticated


SERVICE_URL=$(gcloud run services describe "${NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format="value(status.url)")

echo ""
echo "Done."
echo "  Service URL : ${SERVICE_URL}"
echo ""
echo "Endpoints:"
echo "  ${SERVICE_URL}/health"
echo "  ${SERVICE_URL}/predict"
echo "  ${SERVICE_URL}/pubsub/classify"
echo ""
echo "Test:"
echo "  echo '{\"instances\":[{\"gcs_signed_url\":\"https://...\",\"start_sec\":0}]}' > /tmp/req.json"
echo "  curl -s -X POST ${SERVICE_URL}/predict \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" \\"
echo "    -d @/tmp/req.json"
