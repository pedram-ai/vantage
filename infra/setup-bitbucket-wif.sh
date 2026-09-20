#!/usr/bin/env bash
# Re-point Workload Identity Federation from GitHub to Bitbucket, so the
# pipeline can deploy WITHOUT a service-account key.
#
# Prerequisite: `gcloud auth login` (the session's token expired 2026-09-20 and
# reauth cannot be done non-interactively).
#
#   bash infra/setup-bitbucket-wif.sh <BITBUCKET_WORKSPACE_UUID>
#
# Find the workspace UUID at:
#   Bitbucket → Workspace settings → OpenID Connect → Identity provider URL
#   (https://api.bitbucket.org/2.0/workspaces/<ws>/pipelines-config/identity/oidc)

set -euo pipefail
PROJECT=patexia-vantage
PROJECT_NUM=424459368059
POOL=bitbucket
PROVIDER=bitbucket-provider
SA=vantage-deployer@patexia-vantage.iam.gserviceaccount.com
WS_UUID="${1:?pass the Bitbucket workspace UUID}"
ISSUER="https://api.bitbucket.org/2.0/workspaces/patexia/pipelines-config/identity/oidc"

gcloud iam workload-identity-pools create "$POOL" \
  --project="$PROJECT" --location=global --display-name="Bitbucket" || true

gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --project="$PROJECT" --location=global --workload-identity-pool="$POOL" \
  --display-name="Bitbucket Pipelines" \
  --issuer-uri="$ISSUER" \
  --allowed-audiences="ari:cloud:bitbucket::workspace/$WS_UUID" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repo=assertion.repositoryUuid"

gcloud iam service-accounts add-iam-policy-binding "$SA" --project="$PROJECT" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUM/locations/global/workloadIdentityPools/$POOL/*"

echo "Done. Now uncomment the Deploy step in bitbucket-pipelines.yml."
