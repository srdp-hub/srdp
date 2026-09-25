terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

# Configure the Google Cloud provider with credentials from the environment.
provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
  zone    = var.gcp_zone
}

# Reserve a static external IP address to ensure it doesn't change on reboot.
resource "google_compute_address" "static_ip" {
  name = "${var.instance_name}-ip"
}

# Define the firewall rule to allow public web traffic to our instance.
resource "google_compute_firewall" "allow_http_https" {
  name    = "allow-http-https"
  network = "default"

  # Allow incoming TCP traffic on ports 80 (for ACME challenge) and 443 (for HTTPS).
  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  # Apply this rule to any instance with these tags.
  target_tags   = ["http-server", "https-server"]
  # Allow traffic from anywhere on the internet.
  source_ranges = ["0.0.0.0/0"]
}

# Define the main Compute Engine Virtual Machine.
resource "google_compute_instance" "vm_instance" {
  name         = var.instance_name
  machine_type = "e2-medium"
  # Assign tags that link this VM to our firewall rule.
  tags         = ["http-server", "https-server"]

  boot_disk {
    initialize_params {
      # Use a stable, recent Debian image.
      image = "debian-cloud/debian-12"
    }
  }

  # Configure the network interface and attach our static IP address.
  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.static_ip.address
    }
  }

  # This is the core of the automation:
  # It renders the startup script template, injecting variables from our .tfvars file,
  # and passes the result to the VM to be executed on first boot.
  metadata_startup_script = templatefile("${path.module}/startup-script.sh.tpl", {
    DOMAIN_NAME        = var.domain_name
    REPO_URL           = var.repo_url
    ACME_EMAIL         = var.acme_email
    ZITADEL_MASTERKEY  = var.zitadel_masterkey
    OIDC_CLIENT_ID     = var.oidc_client_id
    OIDC_CLIENT_SECRET = var.oidc_client_secret
    OIDC_COOKIE_SECRET = var.oidc_cookie_secret
  })

  # Assign a service account with broad permissions for this PoC.
  # In a production environment, you would use more fine-grained permissions.
  service_account {
    email  = "default"
    scopes = ["cloud-platform"]
  }

  # Ensure the firewall rule is created before the instance is.
  depends_on = [google_compute_firewall.allow_http_https]
}
