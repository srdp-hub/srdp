#!/usr/bin/env bash
# Idempotent Zitadel OIDC provisioning for the local Docker Compose stack.
# Run whenever a new gated service is added to docker-compose.yml, or any
# time after `docker compose up` to make sure oauth2-proxy's OIDC app has a
# redirect URI for every gated hostname. Closes issue #35: this used to be
# hand-run curl commands from a session transcript, now a real script.
#
# What it does, in order:
#   1. Finds or creates a dedicated admin machine user ("srdp-admin-automation"),
#      using the already-provisioned login-client.pat, which has enough rights
#      to create users and grant org roles even though it can't itself write
#      to projects/apps (see the ADR-style note at the bottom of this file).
#   2. Grants that user ORG_OWNER, generates it a PAT, and stores that PAT in
#      .env as ZITADEL_ADMIN_PAT. Skips all of this if .env already has one.
#   3. Reads the gated hostname list straight out of docker-compose.yml's
#      oauth2-proxy Host() rule (the existing single source of truth, not a
#      second list to keep in sync by hand).
#   4. Fetches the oauth2-proxy OIDC app's current redirect URIs, unions in
#      any hostnames missing a callback, and PUTs the result. No-ops cleanly
#      if nothing changed.
#
# Usage: deploy/docker/provision-oidc.sh   (run from anywhere; requires the
# Compose stack to be up, since it talks to Zitadel over auth.srdp.localhost)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
LOGIN_CLIENT_PAT_FILE="$SCRIPT_DIR/login-client.pat"
AUTH_HOST="auth.srdp.localhost"
ADMIN_USERNAME="srdp-admin-automation"
PROJECT_NAME="srdp"
APP_NAME="oauth2-proxy"

if [ ! -f "$LOGIN_CLIENT_PAT_FILE" ]; then
  echo "error: $LOGIN_CLIENT_PAT_FILE not found. Is the stack up? Has the OIDC bootstrap run?" >&2
  exit 1
fi
LOGIN_CLIENT_PAT=$(cat "$LOGIN_CLIENT_PAT_FILE")

jq_get() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)"; }

# No -f: several call sites need to inspect the response body on non-2xx
# (e.g. Zitadel's code:9 "No changes"), so error handling is explicit below
# rather than delegated to curl.
login_client_api() {
  curl -s -X "$1" "https://$AUTH_HOST/management/v1/$2" \
    -H "Authorization: Bearer $LOGIN_CLIENT_PAT" -H "Content-Type: application/json" \
    ${3:+-d "$3"}
}

admin_api() {
  curl -s -X "$1" "https://$AUTH_HOST/management/v1/$2" \
    -H "Authorization: Bearer $ADMIN_PAT" -H "Content-Type: application/json" \
    ${3:+-d "$3"}
}

# ---------------------------------------------------------------------------
# 1-2. Find or create the admin machine user + PAT, stored in .env
# ---------------------------------------------------------------------------

ADMIN_PAT=$(grep -m1 '^ZITADEL_ADMIN_PAT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)

if [ -z "$ADMIN_PAT" ]; then
  echo "No ZITADEL_ADMIN_PAT in .env, provisioning the admin automation user..."

  USER_ID=$(login_client_api POST "users/_search" \
    "{\"queries\":[{\"userNameQuery\":{\"userName\":\"$ADMIN_USERNAME\",\"method\":\"TEXT_QUERY_METHOD_EQUALS\"}}]}" \
    | jq_get "(d.get('result') or [{}])[0].get('id','')")

  if [ -z "$USER_ID" ]; then
    USER_ID=$(login_client_api POST "users/machine" \
      "{\"userName\":\"$ADMIN_USERNAME\",\"name\":\"SRDP Admin Automation\",\"description\":\"Scripted OIDC provisioning, see deploy/docker/provision-oidc.sh\"}" \
      | jq_get "d['userId']")
    echo "created admin user $USER_ID"
  else
    echo "found existing admin user $USER_ID"
  fi

  IS_OWNER=$(login_client_api POST "orgs/me/members/_search" \
    "{\"queries\":[{\"userIdQuery\":{\"userId\":\"$USER_ID\"}}]}" \
    | jq_get "'ORG_OWNER' in ((d.get('result') or [{}])[0].get('roles') or [])")
  if [ "$IS_OWNER" != "True" ]; then
    login_client_api POST "orgs/me/members" "{\"userId\":\"$USER_ID\",\"roles\":[\"ORG_OWNER\"]}" >/dev/null
    echo "granted ORG_OWNER"
  fi

  ADMIN_PAT=$(login_client_api POST "users/$USER_ID/pats" '{"expirationDate": "2030-01-01T00:00:00Z"}' | jq_get "d['token']")
  {
    echo ""
    echo "# Admin PAT for scripted Zitadel provisioning (deploy/docker/provision-oidc.sh)."
    echo "# Zitadel never re-displays a PAT's value after creation — delete this line to"
    echo "# force the script to mint a fresh one next run, rather than editing it by hand."
    echo "ZITADEL_ADMIN_PAT=$ADMIN_PAT"
  } >>"$ENV_FILE"
  echo "generated admin PAT, saved to .env"
else
  echo "using existing ZITADEL_ADMIN_PAT from .env"
fi

# ---------------------------------------------------------------------------
# 3. Derive the gated hostname list from docker-compose.yml itself
# ---------------------------------------------------------------------------

HOSTS=$(grep 'traefik.http.routers.oauth2-proxy.rule=' "$COMPOSE_FILE" \
  | grep -o 'Host(`[^`]*`)' | sed -E 's/Host\(`([^`]*)`\)/\1/')

if [ -z "$HOSTS" ]; then
  echo "error: could not find any Host() entries on oauth2-proxy's rule line in $COMPOSE_FILE" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 4. Union the desired callbacks into the app's current redirect URIs
# ---------------------------------------------------------------------------

PROJECT_ID=$(admin_api POST "projects/_search" \
  "{\"queries\":[{\"nameQuery\":{\"name\":\"$PROJECT_NAME\",\"method\":\"TEXT_QUERY_METHOD_EQUALS\"}}]}" \
  | jq_get "(d.get('result') or [{}])[0].get('id','')")
if [ -z "$PROJECT_ID" ]; then
  echo "error: project '$PROJECT_NAME' not found — run the OIDC bootstrap first" >&2
  exit 1
fi

APP_ID=$(admin_api POST "projects/$PROJECT_ID/apps/_search" \
  "{\"queries\":[{\"nameQuery\":{\"name\":\"$APP_NAME\",\"method\":\"TEXT_QUERY_METHOD_EQUALS\"}}]}" \
  | jq_get "(d.get('result') or [{}])[0].get('id','')")
if [ -z "$APP_ID" ]; then
  echo "error: app '$APP_NAME' not found in project $PROJECT_ID — run the OIDC bootstrap first" >&2
  exit 1
fi

CURRENT=$(admin_api GET "projects/$PROJECT_ID/apps/$APP_ID")

python3 - "$CURRENT" "$HOSTS" <<'PYEOF' >/tmp/oidc-config-payload.json
import json, sys

current, hosts_raw = json.loads(sys.argv[1]), sys.argv[2].splitlines()
cfg = current["app"]["oidcConfig"]

redirect_uris = set(cfg["redirectUris"])
allowed_origins = set(cfg.get("allowedOrigins") or [])
for host in hosts_raw:
    redirect_uris.add(f"https://{host}/oauth2/callback")
    allowed_origins.add(f"https://{host}")

payload = {
    "redirectUris": sorted(redirect_uris),
    "responseTypes": cfg["responseTypes"],
    "grantTypes": cfg["grantTypes"],
    "appType": "OIDC_APP_TYPE_WEB",
    "authMethodType": "OIDC_AUTH_METHOD_TYPE_BASIC",
    "devMode": cfg["devMode"],
    "allowedOrigins": sorted(allowed_origins),
}
json.dump(payload, sys.stdout)
PYEOF

RESPONSE=$(admin_api PUT "projects/$PROJECT_ID/apps/$APP_ID/oidc_config" "$(cat /tmp/oidc-config-payload.json)")
rm -f /tmp/oidc-config-payload.json

if echo "$RESPONSE" | grep -q '"code":9'; then
  echo "no changes needed, redirect URIs already up to date"
else
  echo "updated redirect URIs for: $(echo "$HOSTS" | tr '\n' ' ')"
fi

# ---------------------------------------------------------------------------
# Why login-client.pat can create the admin user but not update the app
# ---------------------------------------------------------------------------
# Confirmed empirically (see .agents/review/ for the session that found this):
# login-client.pat, scoped to IAM_LOGIN_CLIENT, has enough write access to
# create users and grant org roles (part of its own bootstrap purpose), but
# gets AUTH-5mWD2 "No matching permissions found" on project/app writes. A
# real ORG_OWNER-scoped credential is genuinely required for that, hence
# minting one instead of trying to force login-client.pat further. This is
# a local dev stack; treat this bootstrap chain as dev-only.
