variable "subscription_id" {
  description = "Azure subscription (Azure for Students)."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "canadacentral" # allowed by the Azure for Students region policy, and Postgres B1ms is offered there
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
  description = "replay = recorded example answers only ($0, no key); hybrid = examples from the recordings, any other question live; live = every question live (spend cap and rate limit apply)."
  type        = string
  default     = "replay"
}

variable "model_budget_usd" {
  description = "Monthly cap on model spend enforced by the app (CC_MONTHLY_BUDGET_USD)."
  type        = number
  default     = 10
}

variable "entra_sign_in" {
  description = "true = Microsoft Entra sign-in with directory groups mapped to roles (needs rights to create app registrations and security groups in the tenant); false = the public demo's user switcher."
  type        = bool
  default     = false
}

variable "web_origins" {
  description = "Origins of the web app hosted outside Azure (e.g. Vercel) that may call the API from the browser"
  type        = list(string)
  default     = []
}
