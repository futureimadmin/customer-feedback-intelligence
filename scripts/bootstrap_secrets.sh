#!/usr/bin/env bash
# Add Jira credential versions to Secret Manager (project intelligent-machines).
# Usage:
#   export JIRA_BASE_URL=https://your.atlassian.net
#   export JIRA_EMAIL=bot@example.com
#   export JIRA_API_TOKEN=xxxx
#   ./scripts/bootstrap_secrets.sh

set -euo pipefail
PROJECT="${GCP_PROJECT:-intelligent-machines}"

for pair in \
  "jira-base-url:${JIRA_BASE_URL:?set JIRA_BASE_URL}" \
  "jira-email:${JIRA_EMAIL:?set JIRA_EMAIL}" \
  "jira-api-token:${JIRA_API_TOKEN:?set JIRA_API_TOKEN}"
do
  name="${pair%%:*}"
  val="${pair#*:}"
  echo "Adding version for secret: $name"
  printf '%s' "$val" | gcloud secrets versions add "$name" \
    --project="$PROJECT" \
    --data-file=-
done

echo "Done. Secrets updated in project $PROJECT."
