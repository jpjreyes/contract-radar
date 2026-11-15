# Contract Radar on Azure (decided 2026-11-10; DECISIONS D15).
#
#   Entra ID sign-in ──► Container App "web" (Next.js) ──► Container App "api" (FastAPI, scales to zero)
#                                                              │ managed identity: AcrPull + Key Vault secrets
#                                                              ▼
#                         PostgreSQL Flexible (private: VNet only, pgvector, RLS) ◄── same VNet
#   Application Insights (traces) · Key Vault (secrets) · Blob with immutability (audit chain anchor) · budget alert

data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  name = "${var.prefix}-${random_string.suffix.result}"
  flat = "${var.prefix}${random_string.suffix.result}" # for resources that forbid dashes
  tags = { project = "contract-radar", owner = "jp", env = "demo" }
}

resource "azurerm_resource_group" "rg" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------- observability
resource "azurerm_log_analytics_workspace" "logs" {
  name                = "log-${local.name}"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_application_insights" "ai" {
  name                = "appi-${local.name}"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  workspace_id        = azurerm_log_analytics_workspace.logs.id
  application_type    = "web"
  tags                = local.tags
}

# ---------------------------------------------------------------- network (Postgres is private)
resource "azurerm_virtual_network" "vnet" {
  name                = "vnet-${local.name}"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  address_space       = ["10.40.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "apps" {
  name                 = "snet-apps"
  resource_group_name  = azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.40.0.0/23"]
  delegation {
    name = "aca"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "db" {
  name                 = "snet-db"
  resource_group_name  = azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.40.2.0/24"]
  service_endpoints    = ["Microsoft.Storage"]
  delegation {
    name = "pg"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_private_dns_zone" "pg" {
  name                = "${local.flat}.private.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.rg.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "pg" {
  name                  = "pg-link"
  resource_group_name   = azurerm_resource_group.rg.name
  private_dns_zone_name = azurerm_private_dns_zone.pg.name
  virtual_network_id    = azurerm_virtual_network.vnet.id
}

# ---------------------------------------------------------------- database
resource "random_password" "pg_admin" {
  length  = 32
  special = false
}

resource "random_password" "pg_app" {
  length  = 32
  special = false
}

resource "azurerm_postgresql_flexible_server" "pg" {
  name                          = "psql-${local.name}"
  resource_group_name           = azurerm_resource_group.rg.name
  location                      = azurerm_resource_group.rg.location
  version                       = "17"
  sku_name                      = "B_Standard_B1ms" # burstable: the cheapest tier that runs pgvector comfortably here
  storage_mb                    = 32768
  backup_retention_days         = 7
  delegated_subnet_id           = azurerm_subnet.db.id
  private_dns_zone_id           = azurerm_private_dns_zone.pg.id
  public_network_access_enabled = false
  administrator_login           = "cradmin"
  administrator_password        = random_password.pg_admin.result
  tags                          = local.tags
  depends_on                    = [azurerm_private_dns_zone_virtual_network_link.pg]
}

resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.pg.id
  value     = "VECTOR"
}

resource "azurerm_postgresql_flexible_server_database" "db" {
  name      = "cc_p3"
  server_id = azurerm_postgresql_flexible_server.pg.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# ---------------------------------------------------------------- secrets
resource "azurerm_key_vault" "kv" {
  name                       = "kv-${local.flat}"
  location                   = azurerm_resource_group.rg.location
  resource_group_name        = azurerm_resource_group.rg.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
  tags                       = local.tags
}

resource "azurerm_role_assignment" "kv_operator" { # whoever runs terraform can write the secrets
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

locals {
  pg_host = azurerm_postgresql_flexible_server.pg.fqdn
  secrets = {
    "admin-database-url" = "postgresql://cradmin:${random_password.pg_admin.result}@${local.pg_host}:5432/cc_p3?sslmode=require"
    "app-database-url"   = "postgresql://cc_app:${random_password.pg_app.result}@${local.pg_host}:5432/cc_p3?sslmode=require"
    "app-db-password"    = random_password.pg_app.result
  }
}

resource "azurerm_key_vault_secret" "s" {
  for_each     = local.secrets
  name         = each.key
  value        = each.value
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [azurerm_role_assignment.kv_operator]
}

resource "azurerm_key_vault_secret" "anthropic" { # the real value is set in the portal or with `az keyvault secret set`
  name         = "anthropic-api-key"
  value        = "set-me"
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [azurerm_role_assignment.kv_operator]
  lifecycle { ignore_changes = [value] }
}

# ---------------------------------------------------------------- images and identity
resource "azurerm_container_registry" "acr" {
  name                = "acr${local.flat}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${local.name}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_role_assignment" "kv_read" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# ---------------------------------------------------------------- apps
resource "azurerm_container_app_environment" "env" {
  name                       = "cae-${local.name}"
  location                   = azurerm_resource_group.rg.location
  resource_group_name        = azurerm_resource_group.rg.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.logs.id
  infrastructure_subnet_id   = azurerm_subnet.apps.id
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
  tags = local.tags
}

resource "azurerm_container_app" "api" {
  name                         = "ca-${local.name}-api"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }
  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.app.id
  }
  secret {
    name                = "app-database-url"
    key_vault_secret_id = azurerm_key_vault_secret.s["app-database-url"].versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }
  secret {
    name                = "anthropic-api-key"
    key_vault_secret_id = azurerm_key_vault_secret.anthropic.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 2
    container {
      name   = "api"
      image  = "${azurerm_container_registry.acr.login_server}/contract-radar-api:${var.api_image_tag}"
      cpu    = 1.0
      memory = "2Gi" # the embedding model and the reranker live in memory
      env {
        name        = "APP_DATABASE_URL"
        secret_name = "app-database-url"
      }
      env {
        name        = "ANTHROPIC_API_KEY"
        secret_name = "anthropic-api-key"
      }
      env {
        name  = "CC_BACKEND"
        value = "anthropic"
      }
      env {
        name  = "CC_MODE"
        value = var.demo_mode
      }
      env {
        name  = "CC_MONTHLY_BUDGET_USD"
        value = tostring(var.model_budget_usd)
      }
      env {
        name  = "CC_AUTH"
        value = var.entra_sign_in ? "oidc" : "demo"
      }
      dynamic "env" {
        for_each = var.entra_sign_in ? {
          CC_OIDC_ISSUER   = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/v2.0"
          CC_OIDC_AUDIENCE = azuread_application.api[0].client_id
        } : {}
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = azurerm_application_insights.ai.connection_string
      }
      liveness_probe {
        transport = "HTTP"
        path      = "/healthz"
        port      = 8000
      }
    }
  }
  depends_on = [azurerm_role_assignment.acr_pull, azurerm_role_assignment.kv_read]
}

resource "azurerm_container_app" "web" {
  name                         = "ca-${local.name}-web"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }
  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.app.id
  }
  ingress {
    external_enabled = true
    target_port      = 3000
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
  template {
    min_replicas = 0
    max_replicas = 2
    container {
      name   = "web"
      image  = "${azurerm_container_registry.acr.login_server}/contract-radar-web:${var.web_image_tag}"
      cpu    = 0.5
      memory = "1Gi"
    }
  }
  depends_on = [azurerm_role_assignment.acr_pull]
}

# ---------------------------------------------------------------- audit chain anchor (tamper evidence outside the DB)
resource "azurerm_storage_account" "audit" {
  name                            = "st${local.flat}"
  resource_group_name             = azurerm_resource_group.rg.name
  location                        = azurerm_resource_group.rg.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  blob_properties { versioning_enabled = true }
  tags = local.tags
}

resource "azurerm_storage_container" "anchor" {
  name                  = "audit-anchor"
  storage_account_id    = azurerm_storage_account.audit.id
  container_access_type = "private"
}

resource "azurerm_storage_container_immutability_policy" "anchor" {
  storage_container_resource_manager_id = azurerm_storage_container.anchor.id
  immutability_period_in_days           = 365
  protected_append_writes_all_enabled   = true # anchors are appended, never rewritten
}

resource "azurerm_role_assignment" "anchor_write" {
  scope                = azurerm_storage_account.audit.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# ---------------------------------------------------------------- cost guardrail
resource "azurerm_consumption_budget_resource_group" "budget" {
  name              = "budget-${local.name}"
  resource_group_id = azurerm_resource_group.rg.id
  amount            = var.monthly_budget_usd
  time_grain        = "Monthly"
  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00Z", timestamp())
  }
  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThan"
    contact_emails = [var.alert_email]
  }
  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    contact_emails = [var.alert_email]
  }
  lifecycle { ignore_changes = [time_period] }
}
