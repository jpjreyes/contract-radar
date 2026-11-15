output "resource_group" {
  value = azurerm_resource_group.rg.name
}

output "api_url" {
  value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "web_url" {
  value = "https://${azurerm_container_app.web.ingress[0].fqdn}"
}

output "registry" {
  value = azurerm_container_registry.acr.login_server
}

output "key_vault" {
  value = azurerm_key_vault.kv.name
}

output "web_client_id" {
  description = "For the web app's sign-in (MSAL)."
  value       = var.entra_sign_in ? azuread_application.web[0].client_id : ""
}

output "api_scope" {
  value = "api://contract-radar-${random_string.suffix.result}/access_as_user"
}

output "tenant_id" {
  value = data.azurerm_client_config.current.tenant_id
}

output "identity_map_sql" {
  description = "Run once against the database (admin) so directory groups map to app roles."
  value = join("\n", [for role, g in azuread_group.role :
  "INSERT INTO identity_map VALUES ('group', '${g.object_id}', '${role}') ON CONFLICT DO NOTHING;"])
}

output "entra_sign_in" {
  value = var.entra_sign_in
}
