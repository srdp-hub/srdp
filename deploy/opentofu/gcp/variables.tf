variable "gcp_project_id" {
  type        = string
  description = "The GCP Project ID to deploy resources into."
}

variable "gcp_region" {
  type        = string
  description = "The GCP region to deploy resources into."
  default     = "europe-west4"
}

variable "gcp_zone" {
  type        = string
  description = "The GCP zone for the VM."
  default     = "europe-west4-a"
}

variable "instance_name" {
  type        = string
  description = "The name for the Compute Engine VM."
  default     = "srdp-main"
}


variable "domain_name" {
  type        = string
  description = "The public domain for the application (e.g., yourdomain.com or 1.2.3.4.nip.io)."
}
variable "repo_url" {
  type        = string
  description = "URL of the Git repository to deploy."
}
variable "acme_email" {
  type        = string
  description = "Email address for Let's Encrypt registration."
}
variable "zitadel_masterkey" {
  type        = string
  description = "Master key for Zitadel."
  sensitive   = true
}
variable "oidc_client_id" {
  type        = string
  description = "OIDC client ID from Zitadel."
  sensitive   = true
}
variable "oidc_client_secret" {
  type        = string
  description = "OIDC client secret from Zitadel."
  sensitive   = true
}
variable "oidc_cookie_secret" {
  type        = string
  description = "A long random string for encrypting the oauth2-proxy cookie."
  sensitive   = true
}
