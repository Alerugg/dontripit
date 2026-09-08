#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to the Google Cloud project id before running this script.}"

REGION="${REGION:-europe-southwest1}"
ARTIFACT_REPOSITORY="${ARTIFACT_REPOSITORY:-dontripit}"
GITHUB_REPO="${GITHUB_REPO:-Alerugg/dontripit}"
WIF_POOL="${WIF_POOL:-github}"
WIF_PROVIDER="${WIF_PROVIDER:-dontripit}"
DEPLOYER_SA_ID="${DEPLOYER_SA_ID:-dontripit-deployer}"
RUNTIME_SA_ID="${RUNTIME_SA_ID:-dontripit-runtime}"

DEPLOYER_SA="${DEPLOYER_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_SA="${RUNTIME_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Configuring project ${PROJECT_ID} in ${REGION}..."
gcloud config set project "${PROJECT_ID}" >/dev/null

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com

if ! gcloud artifacts repositories describe "${ARTIFACT_REPOSITORY}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${ARTIFACT_REPOSITORY}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Don’tRipIt production containers"
fi

if ! gcloud iam service-accounts describe "${DEPLOYER_SA}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${DEPLOYER_SA_ID}" \
    --display-name="Don’tRipIt GitHub deployer"
fi

if ! gcloud iam service-accounts describe "${RUNTIME_SA}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${RUNTIME_SA_ID}" \
    --display-name="Don’tRipIt Cloud Run runtime"
fi

for role in roles/run.admin roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role="${role}" \
    --condition=None >/dev/null
done

gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --member="serviceAccount:${DEPLOYER_SA}" \
  --role="roles/iam.serviceAccountUser" >/dev/null

if ! gcloud iam workload-identity-pools describe "${WIF_POOL}" --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${WIF_POOL}" \
    --location=global \
    --display-name="GitHub Actions"
fi

POOL_NAME="$(gcloud iam workload-identity-pools describe "${WIF_POOL}" \
  --location=global \
  --format='value(name)')"

if ! gcloud iam workload-identity-pools providers describe "${WIF_PROVIDER}" \
  --location=global \
  --workload-identity-pool="${WIF_POOL}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${WIF_PROVIDER}" \
    --location=global \
    --workload-identity-pool="${WIF_POOL}" \
    --display-name="Don’tRipIt GitHub provider" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
    --attribute-condition="assertion.repository == '${GITHUB_REPO}'"
fi

gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_NAME}/attribute.repository/${GITHUB_REPO}" >/dev/null

PROVIDER_NAME="$(gcloud iam workload-identity-pools providers describe "${WIF_PROVIDER}" \
  --location=global \
  --workload-identity-pool="${WIF_POOL}" \
  --format='value(name)')"

cat <<EOF

GCP bootstrap complete.

Add these GitHub Actions repository variables:
GCP_PROJECT_ID=${PROJECT_ID}
GCP_REGION=${REGION}
GCP_ARTIFACT_REPOSITORY=${ARTIFACT_REPOSITORY}
GCP_CLOUD_RUN_SERVICE=dontripit-api
GCP_WORKLOAD_IDENTITY_PROVIDER=${PROVIDER_NAME}
GCP_SERVICE_ACCOUNT=${DEPLOYER_SA}
GCP_RUNTIME_SERVICE_ACCOUNT=${RUNTIME_SA}

Then add the application secrets documented in docs/infra/VERCEL_EXIT_PLAN.md.
EOF
