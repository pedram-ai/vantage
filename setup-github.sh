#!/usr/bin/env bash
# One-time: put Vantage on GitHub and turn on CI deploys.
#
# Everything else is already done — the repo is committed locally, the GitHub
# Actions workflow is written, and Workload Identity Federation is configured on
# patexia-vantage (no service-account key exists). This script only needs to
# authenticate you, create the remote, and push.
#
#   bash setup-github.sh
#
# It is safe to re-run: each step is skipped if it is already done.

set -euo pipefail
cd "$(dirname "$0")"

REPO="vantage"

# 1. Authenticate. Opens your browser; the one-time code is copied to your
#    clipboard. Codes expire in ~15 minutes, so run this when you can finish it.
if ! gh auth status >/dev/null 2>&1; then
  echo "==> Signing in to GitHub (approve in the browser that opens)"
  gh auth login --hostname github.com --git-protocol ssh --web --skip-ssh-key
else
  echo "==> Already signed in as $(gh api user --jq .login)"
fi

OWNER="$(gh api user --jq .login)"

# 2. Create the private repo if it does not exist yet.
if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  echo "==> Repo $OWNER/$REPO already exists"
else
  echo "==> Creating private repo $OWNER/$REPO"
  gh repo create "$REPO" --private \
    --description "Vantage — ES/SPY action map, levels and daily map email"
fi

# 3. Point origin at it and push. SSH already authenticates as $OWNER.
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "git@github.com:$OWNER/$REPO.git"
else
  git remote add origin "git@github.com:$OWNER/$REPO.git"
fi
echo "==> Pushing main to $OWNER/$REPO"
git push -u origin main

# 4. The WIF binding was scoped to pedram-ai/vantage. If your login differs,
#    the deploy job cannot impersonate the deployer SA until this is re-bound.
if [ "$OWNER" != "pedram-ai" ]; then
  echo
  echo "!! WIF is bound to 'pedram-ai/vantage' but you pushed as '$OWNER'."
  echo "!! Re-bind so GitHub Actions can deploy:"
  echo
  echo "   gcloud iam service-accounts add-iam-policy-binding \\"
  echo "     vantage-deployer@patexia-vantage.iam.gserviceaccount.com \\"
  echo "     --project=patexia-vantage --role=roles/iam.workloadIdentityUser \\"
  echo "     --member='principalSet://iam.googleapis.com/projects/424459368059/locations/global/workloadIdentityPools/github/attribute.repository/$OWNER/$REPO'"
  echo
fi

echo
echo "==> Done. Watch the first deploy:"
echo "    gh run watch --repo $OWNER/$REPO"
echo "    https://github.com/$OWNER/$REPO/actions"
