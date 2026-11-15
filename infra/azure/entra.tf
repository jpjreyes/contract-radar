# Microsoft Entra ID: who can sign in, and which directory group gives which role.
# The API checks the token (issuer = this tenant, audience = the API app) and maps the person's groups to an app role
# in Postgres (identity_map → resolve_identity), so access is managed as group membership in the directory.
# Off unless var.entra_sign_in: the public demo uses the user switcher instead.

resource "azuread_application" "api" {
  count            = var.entra_sign_in ? 1 : 0
  display_name     = "Contract Radar API (${local.name})"
  sign_in_audience = "AzureADMyOrg"
  identifier_uris  = ["api://contract-radar-${random_string.suffix.result}"]

  group_membership_claims = ["SecurityGroup"] # the token carries the person's security group ids

  api {
    requested_access_token_version = 2
    oauth2_permission_scope {
      id                         = "6f8a0e2b-6a1c-4d5e-9b3f-2c7d8e9f0a11"
      value                      = "access_as_user"
      type                       = "User"
      admin_consent_display_name = "Use Contract Radar"
      admin_consent_description  = "Ask questions and review contract terms as the signed-in user."
      user_consent_display_name  = "Use Contract Radar"
      user_consent_description   = "Ask questions and review contract terms as you."
      enabled                    = true
    }
  }
}

resource "azuread_service_principal" "api" {
  count     = var.entra_sign_in ? 1 : 0
  client_id = azuread_application.api[0].client_id
}

resource "azuread_application" "web" {
  count            = var.entra_sign_in ? 1 : 0
  display_name     = "Contract Radar (${local.name})"
  sign_in_audience = "AzureADMyOrg"
  single_page_application {
    redirect_uris = ["https://${azurerm_container_app.web.ingress[0].fqdn}/", "http://localhost:3072/"]
  }
  required_resource_access {
    resource_app_id = azuread_application.api[0].client_id
    resource_access {
      id   = "6f8a0e2b-6a1c-4d5e-9b3f-2c7d8e9f0a11"
      type = "Scope"
    }
  }
}

resource "azuread_service_principal" "web" {
  count     = var.entra_sign_in ? 1 : 0
  client_id = azuread_application.web[0].client_id
}

# One security group per demo role; membership in the directory decides what each person sees.
locals {
  role_groups = {
    legal_admin = "Contract Radar - Legal operations lead"
    maria       = "Contract Radar - Procurement, North America"
    kenji       = "Contract Radar - Marketing, EMEA and LATAM"
    analyst     = "Contract Radar - Legal analysts (no personal data)"
  }
}

resource "azuread_group" "role" {
  for_each         = var.entra_sign_in ? local.role_groups : {}
  display_name     = each.value
  security_enabled = true
}
