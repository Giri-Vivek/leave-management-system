#!/usr/bin/env bash
# Writes frontend/config.js from Terraform outputs and uploads the site to S3.
set -euo pipefail
cd "$(dirname "$0")/../infra"

REGION=$(terraform output -raw region)
API=$(terraform output -raw api_endpoint)
CLIENT=$(terraform output -raw client_id)
BUCKET=$(terraform output -raw web_bucket)
URL=$(terraform output -raw website_url)

cat > ../frontend/config.js <<CFG
window.APP_CONFIG = { region: "$REGION", clientId: "$CLIENT", apiBase: "$API" };
CFG

aws s3 sync ../frontend "s3://$BUCKET" --delete --exclude ".DS_Store"
echo "Deployed -> $URL"
