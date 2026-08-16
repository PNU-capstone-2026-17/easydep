variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "asia-northeast3"
}

variable "api_vm_name" {
  type    = string
  default = "api-runtime-vm"
}

variable "db_vm_name" {
  type    = string
  default = "state-store-vm"
}

variable "machine_type" {
  type        = string
  description = "GCP machine type for VMs"
}

variable "api_image" {
  type        = string
  description = "Container image for API runtime"
}

variable "db_image" {
  type    = string
  default = "postgres:17-bookworm"
}

variable "db_disk_size_gb" {
  type    = number
  default = 10
}

variable "db_disk_type" {
  type    = string
  default = "pd-standard"
}

variable "postgres_db" {
  type = string
}

variable "postgres_user" {
  type      = string
  sensitive = true
}

variable "postgres_password" {
  type      = string
  sensitive = true
}

resource "google_compute_network" "vpc" {
  name                    = "easydep-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name          = "easydep-subnet"
  ip_cidr_range = "10.0.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
}

resource "google_compute_address" "api_ip" {
  name   = "api-external-ip"
  region = var.region
}

resource "google_compute_firewall" "allow_http" {
  name    = "allow-http"
  network = google_compute_network.vpc.id

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["api-vm"]
}

resource "google_compute_firewall" "allow_postgres" {
  name    = "allow-postgres"
  network = google_compute_network.vpc.id

  allow {
    protocol = "tcp"
    ports    = ["5432"]
  }

  source_tags = ["api-vm"]
  target_tags = ["db-vm"]
}

resource "google_compute_disk" "db_disk" {
  name  = "state-store-disk"
  type  = var.db_disk_type
  zone  = "${var.region}-a"
  size  = var.db_disk_size_gb
}

resource "google_compute_instance" "api_vm" {
  name         = var.api_vm_name
  machine_type = var.machine_type
  zone         = "${var.region}-a"
  tags         = ["api-vm"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-11"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.subnet.id
    access_config {
      nat_ip = google_compute_address.api_ip.address
    }
  }

  metadata_startup_script = templatefile("${path.module}/api_startup.sh.tpl", {
    image          = var.api_image,
    container_port = 8080,
    health_path    = "/health/ready"
  })
}

resource "google_compute_instance" "db_vm" {
  name         = var.db_vm_name
  machine_type = var.machine_type
  zone         = "${var.region}-a"
  tags         = ["db-vm"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-11"
    }
  }

  attached_disk {
    source      = google_compute_disk.db_disk.id
    device_name = "persistent-disk"
    mode        = "READ_WRITE"
  }

  network_interface {
    subnetwork = google_compute_subnetwork.subnet.id
  }

  metadata_startup_script = templatefile("${path.module}/db_startup.sh.tpl", {
    disk_device      = "persistent-disk",
    mount_point      = "/mnt/data",
    container_path   = "/var/lib/postgresql/data",
    image            = var.db_image,
    container_port   = 5432,
    postgres_db      = var.postgres_db,
    postgres_user    = var.postgres_user,
    postgres_password = var.postgres_password
  })
}

output "api_external_ip" {
  value = google_compute_address.api_ip.address
}
