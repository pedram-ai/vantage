#!/usr/bin/env bash
# Manual deploy. Generates the change log from git FIRST so Admin ->
# Documentation always matches what shipped.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/gen_build_info.py app/build_info.json
gcloud run deploy vantage --source . --project=patexia-vantage --region=us-central1 \
  --no-allow-unauthenticated --min-instances=1 \
  --set-env-vars=GOOGLE_CLOUD_PROJECT=patexia-vantage,VANTAGE_ALLOWED_EMAILS=pedram@patexia.com,VANTAGE_BASE_URL=https://argentridge.com,SES_AWS_REGION=us-east-1,"SES_FROM_EMAIL=Argent Ridge <vantage@notify.patexia.com>",SES_REPLY_TO=pedram@patexia.com \
  --set-secrets="AWS_ACCESS_KEY_ID=projects/349112552843/secrets/connect-redesign-aws-access-key-id:latest,AWS_SECRET_ACCESS_KEY=projects/349112552843/secrets/connect-redesign-aws-secret-access-key:latest" \
  --memory=512Mi --cpu=1 --timeout=120 --max-instances=2 --quiet
IMG=$(gcloud run services describe vantage --region=us-central1 --project=patexia-vantage \
      --format='value(spec.template.spec.containers[0].image)')
gcloud run jobs update vantage-daily-run --image="$IMG" --region=us-central1 \
  --project=patexia-vantage --quiet
