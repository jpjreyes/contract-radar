variable "subscription_id" {
  description = "Azure subscription (Azure for Students)."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "westus2"
}

variable "prefix" {
  description = "Short name used in every resource name."
  type        = string
  default     = "cradar"
}

variable "api_image_tag" {
  description = "Tag of the API image in the registry (pushed by scripts/azure_push.sh)."
  type        = string
  default     = "latest"
}

variable "web_image_tag" {
  description = "Tag of the web image in the registry."
  type        = string
  default     = "latest"
}

variable "monthly_budget_usd" {
  description = "Resource-group budget; an email goes out at 80% and 100%."
  type        = number
  default     = 20
}

variable "alert_email" {
  description = "Where budget alerts go."
  type        = string
}

variable "demo_mode" {
  description = "replay = public demo serves recorded answers only; live = model calls (spend cap applies)."
  type        = string
  default     = "replay"
}

variable "model_budget_usd" {
  description = "Monthly cap on model spend enforced by the app (CC_MONTHLY_BUDGET_USD)."
  type        = number
  default     = 10
}
