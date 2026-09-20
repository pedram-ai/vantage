#!/usr/bin/env bash
# Put argentridge.com in front of the Cloud Run service, WITH IAP still enforcing.
#
# ⚠ Cloud Run's built-in --iap only protects the *.run.app URL. A custom domain
# that keeps IAP requires a Global External Application Load Balancer with a
# serverless NEG, IAP enabled on the BACKEND SERVICE, a reserved static IP and
# a Google-managed certificate. That is the documented path; there is no
# cheaper supported way to have both a custom domain and IAP.
#
# 💵 RECURRING COST: the forwarding rule is ~$0.025/hour = ~$18/month, plus a
#    small per-GB charge. This is ABOVE the ≤$10 autonomy envelope, so it needs
#    Pedram's explicit go before running.
#
# Prerequisites: `gcloud auth login`, and argentridge.com registered.
#
#   bash infra/setup-custom-domain.sh argentridge.com

set -euo pipefail
DOMAIN="${1:-argentridge.com}"
PROJECT=patexia-vantage
REGION=us-central1
SERVICE=vantage           # GCP resource id kept; brand is Argent Ridge

gcloud config set project "$PROJECT"

echo "==> Reserving a global static IP"
gcloud compute addresses create argentridge-ip --global --quiet || true
IP=$(gcloud compute addresses describe argentridge-ip --global --format='value(address)')
echo "    IP: $IP"

echo "==> Serverless NEG -> Cloud Run"
gcloud compute network-endpoint-groups create argentridge-neg \
  --region="$REGION" --network-endpoint-type=serverless \
  --cloud-run-service="$SERVICE" --quiet || true

echo "==> Backend service"
gcloud compute backend-services create argentridge-backend \
  --global --load-balancing-scheme=EXTERNAL_MANAGED --quiet || true
gcloud compute backend-services add-backend argentridge-backend \
  --global --network-endpoint-group=argentridge-neg \
  --network-endpoint-group-region="$REGION" --quiet || true

echo "==> Google-managed certificate"
gcloud compute ssl-certificates create argentridge-cert \
  --domains="$DOMAIN,www.$DOMAIN" --global --quiet || true

echo "==> URL map + proxies + forwarding rules"
gcloud compute url-maps create argentridge-lb \
  --default-service argentridge-backend --quiet || true
gcloud compute target-https-proxies create argentridge-https \
  --url-map=argentridge-lb --ssl-certificates=argentridge-cert --quiet || true
gcloud compute forwarding-rules create argentridge-fr-https \
  --global --target-https-proxy=argentridge-https --ports=443 \
  --address=argentridge-ip --load-balancing-scheme=EXTERNAL_MANAGED --quiet || true

# http -> https redirect
cat > /tmp/ar-redirect.yaml <<YAML
kind: compute#urlMap
name: argentridge-redirect
defaultUrlRedirect:
  redirectResponseCode: MOVED_PERMANENTLY_DEFAULT
  httpsRedirect: true
YAML
gcloud compute url-maps import argentridge-redirect --source=/tmp/ar-redirect.yaml --global --quiet || true
gcloud compute target-http-proxies create argentridge-http \
  --url-map=argentridge-redirect --quiet || true
gcloud compute forwarding-rules create argentridge-fr-http \
  --global --target-http-proxy=argentridge-http --ports=80 \
  --address=argentridge-ip --load-balancing-scheme=EXTERNAL_MANAGED --quiet || true

echo "==> Enabling IAP on the backend service"
gcloud compute backend-services update argentridge-backend --global --iap=enabled --quiet
gcloud iap web add-iam-policy-binding \
  --resource-type=backend-services --service=argentridge-backend \
  --member=user:pedram@patexia.com --role=roles/iap.httpsResourceAccessor

cat <<TXT

==> DNS — add these in Cloudflare, PROXY OFF (grey cloud):

      A     @      $IP
      A     www    $IP

   ⚠ Cloudflare's orange-cloud proxy must stay OFF, at least until the
     Google-managed certificate is ACTIVE. Proxying breaks the HTTP-01
     domain validation and can also interfere with IAP's OAuth redirect.

   Certificate goes ACTIVE 15-60 min after DNS resolves. Watch it with:
     gcloud compute ssl-certificates describe argentridge-cert --global \\
       --format='value(managed.status,managed.domainStatus)'
TXT
