variable "resource_prefix" {
  type    = string
  default = "easydep"
}

variable "vm_sku" {
  type    = string
  default = "t3.small"
}

variable "ssh_public_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "boot_image_id" {
  type = string
  validation {
    condition     = startswith(var.boot_image_id, "ami-")
    error_message = "boot_image_id must be an explicit AMI id."
  }
}

variable "image_digest_course_registration_app" {
  type = string
  validation {
    condition     = startswith(var.image_digest_course_registration_app, "sha256:")
    error_message = "image_digest_course_registration_app must be an immutable sha256 digest."
  }
}

variable "container_port_course_registration_app_course_registration_app_http" {
  type = number
}

variable "secret_reference_course_registration_app_course_registration_db_credential" {
  type      = string
  sensitive = true
}

variable "secret_reference_course_registration_db_runtime_credential" {
  type      = string
  sensitive = true
}
