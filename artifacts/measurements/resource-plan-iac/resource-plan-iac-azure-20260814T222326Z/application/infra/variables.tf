variable "resource_group_name" {
  description = "Name of the Azure Resource Group"
  type        = string
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "koreacentral"
}

variable "admin_username" {
  description = "Admin username for the Linux VMs"
  type        = string
  default     = "azureuser"
}

variable "admin_ssh_key" {
  description = "Public SSH key for the admin user"
  type        = string
}

variable "api_vm_size" {
  description = "Size of the VM hosting the API runtime"
  type        = string
  # No default – selection is deferred, user must provide a suitable size
}

variable "state_vm_size" {
  description = "Size of the VM hosting the PostgreSQL state store"
  type        = string
  # No default – selection is deferred, user must provide a suitable size
}

variable "api_container_image" {
  description = "Docker image for the API runtime"
  type        = string
}

variable "postgres_image" {
  description = "Docker image for PostgreSQL"
  type        = string
  default     = "postgres:17-bookworm"
}

variable "postgres_db" {
  description = "PostgreSQL database name"
  type        = string
}

variable "postgres_user" {
  description = "PostgreSQL user name"
  type        = string
  sensitive   = true
}

variable "postgres_password" {
  description = "PostgreSQL password"
  type        = string
  sensitive   = true
}

variable "api_port" {
  description = "Port on which the API container listens"
  type        = number
  default     = 8080
}

variable "postgres_port" {
  description = "Port on which PostgreSQL listens"
  type        = number
  default     = 5432
}
