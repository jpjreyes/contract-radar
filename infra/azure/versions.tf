terraform {
  required_version = ">= 1.9"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 4.0" }
    azuread = { source = "hashicorp/azuread", version = "~> 3.0" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # State stays local for the demo (one operator). For a team: an azurerm backend in a locked storage account.
}

provider "azurerm" {
  features {
    key_vault { purge_soft_delete_on_destroy = false }
    resource_group { prevent_deletion_if_contains_resources = false }
  }
  subscription_id = var.subscription_id
}

provider "azuread" {}
