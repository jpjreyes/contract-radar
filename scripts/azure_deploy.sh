#!/usr/bin/env bash
# Deploy Contract Radar to Azure (DECISIONS D15). Run from the repo root after:
#   az login                      # your Azure for Students account
#   cp infra/azure/terraform.tfvars.example infra/azure/terraform.tfvars   # fill subscription_id + alert_email
#
# Steps (each is idempotent; re-run the script after a failure):
#   1. infrastructure without the apps (network, Postgres, Key Vault, registry, identity, Entra, storage, budget)
#   2. build the API image locally for linux/amd64 (Colima) and push it to the registry (Azure for Students
#      subscriptions don't allow ACR Tasks, so `az acr build` is not an option)
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

# Docker login to the registry with a short-lived Entra token, in a private config (never the default ~/.docker)
export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.colima/default/docker.sock}"
export DOCKER_CONFIG="$(mktemp -d)"
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
[ -d "$HOME/.docker/cli-plugins" ] && ln -s "$HOME/.docker/cli-plugins" "$DOCKER_CONFIG/cli-plugins"  # keep buildx
az acr login --name "${ACR%%.*}" --expose-token --query accessToken -o tsv |
  docker login "$ACR" -u 00000000-0000-0000-0000-000000000000 --password-stdin >/dev/null
build_push() {  # build_push <image> <context> [docker build args...]
  local image=$1 ctx=$2; shift 2
  docker buildx build --platform linux/amd64 --provenance=false --push \
    -t "$ACR/$image:$TAG" -t "$ACR/$image:latest" "$@" "$ctx"
}

echo "== 2/4 API image ($TAG), linux/amd64"
build_push contract-radar-api .

echo "== 3/4 apps"
$TF apply -input=false -auto-approve -var "api_image_tag=$TAG" -target=azurerm_container_app.api
API_URL=$($TF output -raw api_url)
# the web app's Entra registration needs the web URL, which needs the web app: create it with a placeholder image
# tag the first time, then build the real image with the ids it needs and roll it out
$TF apply -input=false -auto-approve -var "api_image_tag=$TAG" -var "web_image_tag=$TAG" || true
WEB_CLIENT_ID=$($TF output -raw web_client_id)
if [ -n "$WEB_CLIENT_ID" ]; then  # entra_sign_in = true: Microsoft sign-in
  AUTH_ARGS=(--build-arg NEXT_PUBLIC_AUTH_MODE=entra --build-arg "NEXT_PUBLIC_ENTRA_CLIENT_ID=$WEB_CLIENT_ID"
    --build-arg "NEXT_PUBLIC_ENTRA_TENANT_ID=$($TF output -raw tenant_id)" --build-arg "NEXT_PUBLIC_API_SCOPE=$($TF output -raw api_scope)")
else                              # public demo: the user switcher
  AUTH_ARGS=(--build-arg NEXT_PUBLIC_AUTH_MODE=demo)
fi
build_push contract-radar-web web --build-arg "API_URL=$API_URL" "${AUTH_ARGS[@]}"
$TF apply -input=false -auto-approve -var "api_image_tag=$TAG" -var "web_image_tag=$TAG"

echo "== 4/4 data"
scripts/azure_load_db.sh

echo
echo "API: $API_URL/healthz"
echo "Web: $($TF output -raw web_url)"
[ -n "$WEB_CLIENT_ID" ] && echo "Next: add yourself to one of the 'Contract Radar - ...' groups in Entra ID, then sign in."
echo "      Set the model key when you want live answers: az keyvault secret set --vault-name $($TF output -raw key_vault) --name anthropic-api-key --value <key>"
