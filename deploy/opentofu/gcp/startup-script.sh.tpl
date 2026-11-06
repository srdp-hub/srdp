#!/bin/bash
# startup-script.sh.tpl
#
# This script is a template executed by a new GCP Compute Engine instance.
# It bootstraps the entire application stack: Docker, Git, and Docker Compose.
# Values prefixed with '$' are injected by OpenTofu via the 'templatefile' function.

# --- Script Configuration ---
# Exit immediately if a command exits with a non-zero status.
set -e
# Print each command to the console before it is executed.
set -x

echo "--- Phase 1: Installing System Dependencies ---"

# Set frontend to noninteractive to prevent apt-get from hanging on prompts
export DEBIAN_FRONTEND=noninteractive

# Update package lists and install prerequisite software
apt-get update -y
apt-get install -yq ca-certificates curl gnupg git

echo "--- Phase 2: Installing Docker and Docker Compose ---"

# Add Docker's official GPG key
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

# Set up the Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine and Docker Compose Plugin
apt-get update -y
apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "--- Phase 3: Cloning Repository and Configuring Application ---"

# Clone the application repository from the URL provided by OpenTofu
git clone "${REPO_URL}" "/opt/app"
cd "/opt/app"
git checkout dev
cd "docker"



mkdir -p certs

openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout certs/selfsigned.key \
  -out certs/selfsigned.crt \
  -subj "/C=US/ST=State/L=City/O=Organization/OU=OrgUnit/CN=auth.srdp.localhost"

# Create the .env file from secrets and configuration passed by OpenTofu.
# This file provides environment variables to Docker Compose.
cat <<EOF > .env
SRDP_DOMAIN=${DOMAIN_NAME}
ACME_EMAIL=${ACME_EMAIL}
ZITADEL_MASTERKEY=${ZITADEL_MASTERKEY}
OAUTH2_PROXY_CLIENT_ID=${OIDC_CLIENT_ID}
OAUTH2_PROXY_CLIENT_SECRET=${OIDC_CLIENT_SECRET}
OAUTH2_PROXY_COOKIE_SECRET=${OIDC_COOKIE_SECRET}
EOF

# Create the directory for persistent storage of Let's Encrypt certificates
mkdir -p letsencrypt

echo "--- Phase 4: Launching Application Stack with Docker Compose ---"

chmod -R 777 .

# Start all services defined in the compose files in detached mode.
# The --build flag ensures any changes to custom Dockerfiles are applied.
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d

echo "--- Deployment script completed successfully ---"
