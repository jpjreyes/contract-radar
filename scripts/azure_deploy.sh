#!/usr/bin/env bash
# Deploy Contract Radar to Azure (DECISIONS D15). Run from the repo root after:
#   az login                      # your Azure for Students account
#   cp infra/azure/terraform.tfvars.example infra/azure/terraform.tfvars   # fill subscription_id + alert_email
#
# Steps (each is idempotent; re-run the script after a failure):
#   1. infrastructure without the apps (network, Postgres, Key Vault, registry, identity, Entra, storage, budget)
#   2. build the API image in Azure (az acr build: nothing builds on this Mac)
#   3. the API app, then the web image (it needs the API URL and the Entra ids), then the web app
#   4. load the data (scripts/azure_load_db.sh) and print the URLs
set -euo pipefail
cd "$(dirname "$0")/.."
TF="terraform -chdir=infra/azure"
TAG="${TAG:-$(git rev-parse --short HEAD)}"

echo "== 1/4 infrastructure"
$TF init -input=false >/dev/null
$TF apply -input=false -auto-approve \
  -target=azurerm_container_registry.acr -target=azurerm_key_vault_secret.s -target=azurerm_key_vault_secret.anthropic \
  -target=azurerm_postgresql_flexible_server_database.db -target=azurerm_postgresql_flexible_server_configuration.extensions \
  -target=azurerm_container_app_environment.env -target=azurerm_role_assignment.acr_pull \
  -target=azurerm_role_assignment.kv_read -target=azuread_group.role -target=azuread_application.api \
  -target=azurerm_storage_container_immutability_policy.anchor -target=azurerm_consumption_budget_resource_group.budget
ACR=$($TF output -raw registry)

echo "== 2/4 API image ($TAG), built in Azure"
az acr build --registry "${ACR%%.*}" --image "contract-radar-api:$TAG" --image contract-radar-api:latest . >/dev/null

echo "== 3/4 apps"
$TF apply -input=false -auto-approve -var "api_image_tag=$TAG" -target=azurerm_container_app.api
API_URL=$($TF output -raw api_url)
# the web app's Entra registration needs the web URL, which needs the web app: create it with a placeholder image
# tag the first time, then build the real image with the ids it needs and roll it out
$TF apply -input=false -auto-approve -var "api_image_tag=$TAG" -var "web_image_tag=$TAG" || true
az acr build --registry "${ACR%%.*}" --image "contract-radar-web:$TAG" --image contract-radar-web:latest \
  --build-arg "API_URL=$API_URL" --build-arg NEXT_PUBLIC_AUTH_MODE=entra \
  --build-arg "NEXT_PUBLIC_ENTRA_CLIENT_ID=$($TF output -raw web_client_id)" \
  --build-arg "NEXT_PUBLIC_ENTRA_TENANT_ID=$($TF output -raw tenant_id)" \
  --build-arg "NEXT_PUBLIC_API_SCOPE=$($TF output -raw api_scope)" web >/dev/null
$TF apply -input=false -auto-approve -var "api_image_tag=$TAG" -var "web_image_tag=$TAG"

echo "== 4/4 data"
scripts/azure_load_db.sh

echo
echo "API: $API_URL/healthz"
echo "Web: $($TF output -raw web_url)"
echo "Next: add yourself to one of the 'Contract Radar - ...' groups in Entra ID, then sign in."
echo "      Set the model key when you want live answers: az keyvault secret set --vault-name $($TF output -raw key_vault) --name anthropic-api-key --value <key>"
