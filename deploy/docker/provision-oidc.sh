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
#   3. Reads the gated hostname list straight out of the oauth2-proxy router rule
#      in config/traefik/dynamic/srdp.yml (the single source of truth, not a
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

# Change .env in place: set KEY=VALUE, or remove KEY when the value is "-unset".
# The value goes through the environment, so a secret never shows in the process list,
# and the file is replaced in one step, so an interrupted run cannot truncate it.
edit_env() {
  ENV_EDIT_VALUE="$2" python3 - "$ENV_FILE" "$1" <<'EOF'
import os
import sys
import tempfile

path, key = sys.argv[1:3]
value = os.environ["ENV_EDIT_VALUE"]
exists = os.path.exists(path)
lines = open(path).read().splitlines() if exists else []
if value == "-unset":
    out = [line for line in lines if not line.startswith(f"{key}=")]
elif any(line.startswith(f"{key}=") for line in lines):
    out = [f"{key}={value}" if line.startswith(f"{key}=") else line for line in lines]
else:
    out = [*lines, f"{key}={value}"]
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)))
with os.fdopen(fd, "w") as handle:
    handle.write("\n".join(out) + "\n")
os.chmod(tmp, os.stat(path).st_mode if exists else 0o600)
os.replace(tmp, path)
EOF
}
set_env() { edit_env "$1" "$2"; }
unset_env() { edit_env "$1" "-unset"; }

ADMIN_PAT=$(grep -m1 '^ZITADEL_ADMIN_PAT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)

# A PAT from a Zitadel that was reset since (a wiped volume) is rejected, so mint a new one.
# Any other answer, such as a stopped Zitadel, must not throw a valid PAT away.
if [ -n "$ADMIN_PAT" ]; then
  STATUS=$(curl -s -o /dev/null -w '%{http_code}' "https://$AUTH_HOST/management/v1/orgs/me" -H "Authorization: Bearer $ADMIN_PAT") || STATUS=000
  case "$STATUS" in
    200) ;;
    401 | 403)
      echo "ZITADEL_ADMIN_PAT in .env is not accepted (HTTP $STATUS), minting a new one..."
      unset_env ZITADEL_ADMIN_PAT
      ADMIN_PAT=""
      ;;
    *)
      echo "error: Zitadel answered HTTP $STATUS at https://$AUTH_HOST, is the stack up?" >&2
      exit 1
      ;;
  esac
fi

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
# 3. Derive the gated hostname list from the Traefik routing file
# ---------------------------------------------------------------------------

# The oauth2-proxy router in the Traefik routing file lists the gated hostnames.
# Its Host() rules use the SRDP_DOMAIN template, which is the auth host without "auth.".
TRAEFIK_FILE="$SCRIPT_DIR/../../config/traefik/dynamic/srdp.yml"
SRDP_DOMAIN="${AUTH_HOST#auth.}"
HOSTS=$(awk '/^    oauth2-proxy:$/ {found=1} found && /rule:/ {print; exit}' "$TRAEFIK_FILE" \
  | grep -o 'Host(`[^`]*`)' | sed -E 's/Host\(`([^`]*)`\)/\1/' \
  | sed "s/{{ env \"SRDP_DOMAIN\" }}/$SRDP_DOMAIN/" || true)

if [ -z "$HOSTS" ]; then
  echo "error: could not find any Host() entries on the oauth2-proxy rule in $TRAEFIK_FILE" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 4. Union the desired callbacks into the app's current redirect URIs
# ---------------------------------------------------------------------------

PROJECT_ID=$(admin_api POST "projects/_search" \
  "{\"queries\":[{\"nameQuery\":{\"name\":\"$PROJECT_NAME\",\"method\":\"TEXT_QUERY_METHOD_EQUALS\"}}]}" \
  | jq_get "(d.get('result') or [{}])[0].get('id','')")
if [ -z "$PROJECT_ID" ]; then
  PROJECT_ID=$(admin_api POST "projects" "{\"name\":\"$PROJECT_NAME\"}" | jq_get "d['id']")
  echo "created project '$PROJECT_NAME' ($PROJECT_ID)"
fi

APP_ID=$(admin_api POST "projects/$PROJECT_ID/apps/_search" \
  "{\"queries\":[{\"nameQuery\":{\"name\":\"$APP_NAME\",\"method\":\"TEXT_QUERY_METHOD_EQUALS\"}}]}" \
  | jq_get "(d.get('result') or [{}])[0].get('id','')")
NEW_CREDENTIALS=""
if [ -z "$APP_ID" ]; then
  FIRST_HOST=$(echo "$HOSTS" | head -n1)
  CREATED=$(admin_api POST "projects/$PROJECT_ID/apps/oidc" \
    "{\"name\":\"$APP_NAME\",\"redirectUris\":[\"https://$FIRST_HOST/oauth2/callback\"],\"responseTypes\":[\"OIDC_RESPONSE_TYPE_CODE\"],\"grantTypes\":[\"OIDC_GRANT_TYPE_AUTHORIZATION_CODE\"],\"appType\":\"OIDC_APP_TYPE_WEB\",\"authMethodType\":\"OIDC_AUTH_METHOD_TYPE_BASIC\"}")
  APP_ID=$(echo "$CREATED" | jq_get "d['appId']")
  set_env OIDC_CLIENT_ID "$(echo "$CREATED" | jq_get "d['clientId']")"
  set_env OIDC_CLIENT_SECRET "$(echo "$CREATED" | jq_get "d['clientSecret']")"
  NEW_CREDENTIALS=1
  echo "created app '$APP_NAME' ($APP_ID), saved its client ID and secret to .env"
fi

CURRENT=$(admin_api GET "projects/$PROJECT_ID/apps/$APP_ID")

# The app is the source of truth. When .env holds another client ID, issue a new secret and save both.
APP_CLIENT_ID=$(echo "$CURRENT" | jq_get "d['app']['oidcConfig']['clientId']")
ENV_CLIENT_ID=$(grep -m1 '^OIDC_CLIENT_ID=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
if [ "$APP_CLIENT_ID" != "$ENV_CLIENT_ID" ]; then
  SECRET=$(admin_api POST "projects/$PROJECT_ID/apps/$APP_ID/oidc_config/_generate_client_secret" "{}" | jq_get "d['clientSecret']")
  set_env OIDC_CLIENT_ID "$APP_CLIENT_ID"
  set_env OIDC_CLIENT_SECRET "$SECRET"
  NEW_CREDENTIALS=1
  echo ".env had another client ID, saved the client ID and a new secret of '$APP_NAME'"
fi

PAYLOAD=$(python3 - "$CURRENT" "$HOSTS" <<'PYEOF'
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
    "devMode": cfg.get("devMode", False),
    "allowedOrigins": sorted(allowed_origins),
}
json.dump(payload, sys.stdout)
PYEOF
)

RESPONSE=$(admin_api PUT "projects/$PROJECT_ID/apps/$APP_ID/oidc_config" "$PAYLOAD")

if echo "$RESPONSE" | grep -q '"code":9'; then
  echo "no changes needed, redirect URIs already up to date"
else
  echo "updated redirect URIs for: $(echo "$HOSTS" | tr '\n' ' ')"
fi

if [ -n "$NEW_CREDENTIALS" ]; then
  echo "OIDC_CLIENT_ID and OIDC_CLIENT_SECRET changed in .env, run 'just docker-up -d' to recreate oauth2-proxy with them"
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
