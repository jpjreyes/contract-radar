#!/usr/bin/env bash
# Load the local database's data into Azure Postgres (private: reachable only from inside the VNet).
#   1. export the data locally (cloud_load export) (contracts, chunks with masked text and embeddings, extracted fields)
#   2. upload the dump to a private blob container (your az login, Entra auth: no storage keys)
#   3. run a one-off Container Apps job inside the VNet with the API image: create the schema, restore the data,
#      then set up roles, row-level security, audit, review queue, limits and sign-in, and map the Entra groups
# Order matters: data is restored BEFORE row-level security is switched on (the managed admin is not a superuser).
set -euo pipefail
cd "$(dirname "$0")/.."
TF="terraform -chdir=infra/azure"
set -a; source .env; set +a  # local DATABASE_URL (admin)

RG=$($TF output -raw resource_group)
ACR=$($TF output -raw registry)
KV=$($TF output -raw key_vault)
ST=$(az storage account list -g "$RG" --query "[0].name" -o tsv)
ENV=$(az containerapp env list -g "$RG" --query "[0].name" -o tsv)
ID=$(az identity list -g "$RG" --query "[0].id" -o tsv)
CLIENT=$(az identity list -g "$RG" --query "[0].clientId" -o tsv)
DUMP=/tmp/contract-radar-data.tar.gz

echo "== export (contracts, chunks with masked text + embeddings, fields)"
uv run python -m contract_copilot.cloud_load export "$DUMP"
ls -lh "$DUMP"

echo "== upload"
az storage container create --account-name "$ST" --name dbload --auth-mode login >/dev/null
az storage blob upload --account-name "$ST" --container-name dbload --name data.tar.gz --file "$DUMP" \
  --auth-mode login --overwrite >/dev/null

echo "== restore + setup, inside the VNet"
SQL=$($TF output -raw identity_map_sql | tr '\n' ' ')
az containerapp job create -g "$RG" -n "job-load" --environment "$ENV" --trigger-type Manual \
  --replica-timeout 3600 --replica-retry-limit 0 --cpu 1 --memory 2Gi \
  --image "$ACR/contract-radar-api:latest" --registry-server "$ACR" --registry-identity "$ID" \
  --mi-user-assigned "$ID" \
  --secrets "admin-url=keyvaultref:https://$KV.vault.azure.net/secrets/admin-database-url,identityref:$ID" \
            "app-pw=keyvaultref:https://$KV.vault.azure.net/secrets/app-db-password,identityref:$ID" \
            "app-url=keyvaultref:https://$KV.vault.azure.net/secrets/app-database-url,identityref:$ID" \
  --env-vars "DATABASE_URL=secretref:admin-url" "APP_DB_PASSWORD=secretref:app-pw" "APP_DATABASE_URL=secretref:app-url" \
             "AZURE_CLIENT_ID=$CLIENT" \
             "BLOB_URL=https://$ST.blob.core.windows.net/dbload/data.tar.gz" "IDENTITY_SQL=$SQL" \
  --command "uv" --args "run" "--no-sync" "python" "-m" "contract_copilot.cloud_load" >/dev/null 2>&1 || \
az containerapp job update -g "$RG" -n "job-load" --image "$ACR/contract-radar-api:latest" >/dev/null
az containerapp job start -g "$RG" -n "job-load" >/dev/null
echo "started: follow it with  az containerapp job execution list -g $RG -n job-load -o table"
