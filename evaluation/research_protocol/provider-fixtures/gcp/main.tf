terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "=5.45.2"
    }
  }
}

provider "google" {
  project = "easydep-component-audit"
  region  = "asia-northeast3"
}

resource "google_compute_network" "main" {
  name                    = "easydep-component-audit"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "main" {
  name          = "easydep-component-audit"
  ip_cidr_range = "10.82.0.0/24"
  region        = "asia-northeast3"
  network       = google_compute_network.main.id
}

resource "google_compute_firewall" "health" {
  name    = "easydep-component-audit-health"
  network = google_compute_network.main.name

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }

  source_ranges = ["35.191.0.0/16", "130.211.0.0/22"]
  target_tags   = ["easydep-component-audit"]
}

resource "google_compute_instance" "app_a" {
  name         = "easydep-component-audit-a"
  machine_type = "e2-medium"
  zone         = "asia-northeast3-a"
  tags         = ["easydep-component-audit"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.main.id
  }

  lifecycle {
    ignore_changes = [attached_disk]
  }
}

resource "google_compute_instance" "app_c" {
  name         = "easydep-component-audit-c"
  machine_type = "e2-medium"
  zone         = "asia-northeast3-c"
  tags         = ["easydep-component-audit"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.main.id
  }
}

resource "google_compute_disk" "notes" {
  name = "easydep-component-audit-notes"
  type = "pd-balanced"
  zone = google_compute_instance.app_a.zone
  size = 20
}

resource "google_compute_attached_disk" "notes" {
  disk        = google_compute_disk.notes.id
  instance    = google_compute_instance.app_a.id
  device_name = "notes"
  mode        = "READ_WRITE"
}

resource "google_compute_instance_group" "app_a" {
  name      = "easydep-component-audit-a"
  zone      = google_compute_instance.app_a.zone
  instances = [google_compute_instance.app_a.id]

  named_port {
    name = "http"
    port = 8080
  }
}

resource "google_compute_instance_group" "app_c" {
  name      = "easydep-component-audit-c"
  zone      = google_compute_instance.app_c.zone
  instances = [google_compute_instance.app_c.id]

  named_port {
    name = "http"
    port = 8080
  }
}

resource "google_compute_health_check" "app" {
  name = "easydep-component-audit"

  http_health_check {
    port         = 8080
    request_path = "/health"
  }
}

resource "google_compute_backend_service" "app" {
  name          = "easydep-component-audit"
  protocol      = "HTTP"
  port_name     = "http"
  timeout_sec   = 30
  health_checks = [google_compute_health_check.app.id]

  backend {
    group = google_compute_instance_group.app_a.id
  }

  backend {
    group = google_compute_instance_group.app_c.id
  }
}

resource "google_compute_url_map" "app" {
  name            = "easydep-component-audit"
  default_service = google_compute_backend_service.app.id
}

resource "google_compute_target_http_proxy" "app" {
  name    = "easydep-component-audit-http"
  url_map = google_compute_url_map.app.id
}

resource "google_compute_managed_ssl_certificate" "app" {
  name = "easydep-component-audit"

  managed {
    domains = ["component-audit.example.invalid"]
  }
}

resource "google_compute_target_https_proxy" "app" {
  name             = "easydep-component-audit-https"
  url_map          = google_compute_url_map.app.id
  ssl_certificates = [google_compute_managed_ssl_certificate.app.id]
}

resource "google_compute_global_forwarding_rule" "http" {
  name       = "easydep-component-audit-http"
  target     = google_compute_target_http_proxy.app.id
  port_range = "80"
}

resource "google_compute_global_forwarding_rule" "https" {
  name       = "easydep-component-audit-https"
  target     = google_compute_target_https_proxy.app.id
  port_range = "443"
}
