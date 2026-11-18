"""Generate the credentials of a local stack, once, so no password lives in a tracked file.

Compose reads them from ``deploy/docker/.env``, and kind from Secrets with the fixed names that an
operator creates on any other cluster. ``deploy/secrets.toml`` is the one list of both. A value that
exists is never replaced, so a redeploy keeps the passwords that the databases already hold.

Run through ``just``: ``docker-secrets``, ``local-secrets`` and ``local-oidc``.
"""

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import ssl
import string
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_ALNUM = string.ascii_letters + string.digits


def _alnum(length: int) -> str:
    return "".join(secrets.choice(_ALNUM) for _ in range(length))


def _zitadel_password() -> str:
    """Zitadel wants an uppercase letter, a lowercase letter, a digit and a symbol."""
    chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!%+-_"),
        *(secrets.choice(_ALNUM) for _ in range(20)),
    ]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _generator(fmt: str, env: str) -> Callable[[], str]:
    """Return the generator for a ``format`` from ``deploy/secrets.toml``."""
    kind, _, size = fmt.partition(":")
    if kind in {"alnum", "hex"} and size.isdigit() and int(size) > 0:
        n = int(size)
        return (lambda: _alnum(n)) if kind == "alnum" else (lambda: secrets.token_hex(n))
    fixed = {
        "garage-key-id": lambda: "GK" + secrets.token_hex(12),
        "zitadel-password": _zitadel_password,
        "placeholder": lambda: f"CHANGE_ME_{env}",
    }
    if size or kind not in fixed:
        msg = f"Unknown format '{fmt}' for {env}"
        raise ValueError(msg)
    return fixed[kind]


@dataclass(frozen=True)
class LocalSecret:
    """One credential: its Secret and key in kind, its variable in the Compose ``.env``."""

    secret: str
    key: str
    format: str

    @property
    def env(self) -> str:
        """The ``.env`` variable: the Secret without ``srdp-`` plus the key, ``srdp-zitadel/db-password`` is ``ZITADEL_DB_PASSWORD``."""
        return f"{self.secret.removeprefix('srdp-')}_{self.key}".upper().replace("-", "_")

    def make(self) -> str:
        """Generate a new value in this credential's format."""
        return _generator(self.format, self.env)()


REPO_ROOT = Path(__file__).resolve().parents[3]
SECRETS_FILE = REPO_ROOT / "deploy" / "secrets.toml"
TRAEFIK_ROUTES = REPO_ROOT / "config" / "traefik" / "dynamic" / "srdp.yml"


def load_secrets(path: Path = SECRETS_FILE) -> tuple[LocalSecret, ...]:
    """Read the credentials from ``deploy/secrets.toml`` and check every format.

    Args:
        path: The TOML file with a ``[[secret]]`` table per credential.

    Returns:
        The credentials in file order.

    Raises:
        ValueError: If an entry has an unknown format, or two entries share a Secret key or a variable.
    """
    with path.open("rb") as f:
        entries = tomllib.load(f).get("secret", [])
    specs = tuple(LocalSecret(e["secret"], e["key"], e["format"]) for e in entries)
    for spec in specs:
        _generator(spec.format, spec.env)
    if len({(s.secret, s.key) for s in specs}) != len(specs) or len({s.env for s in specs}) != len(specs):
        msg = f"{path} lists a Secret key or a variable twice"
        raise ValueError(msg)
    return specs


# Helm deletes a Secret that an older chart rendered, unless the live object says to keep it.
_KEEP = {"helm.sh/resource-policy": "keep"}

APPLY_ARGS = ("--server-side", "--field-manager=srdp-local-secrets", "--force-conflicts", "--filename", "-")

_ENV_LINE = re.compile(r"(?P<prefix>\s*(?:export\s+)?)(?P<name>\w+)\s*=(?P<value>.*)")


def fill_env(text: str, specs: tuple[LocalSecret, ...]) -> tuple[str, list[str]]:
    """Fill every empty or missing credential in ``.env`` text, and leave the rest as it is.

    Args:
        text: The current ``.env`` content.
        specs: The credentials from ``deploy/secrets.toml``.

    Returns:
        The new content and the names of the variables that were filled.
    """
    wanted = {s.env: s for s in specs}
    lines = text.splitlines()
    last = _last_env_lines(lines)
    filled: list[str] = []
    for name, spec in wanted.items():
        if name not in last:
            lines.append(f"{name}={spec.make()}")
            filled.append(name)
            continue
        i, match = last[name]
        if not _env_scalar(match["value"]):
            lines[i] = f"{match['prefix']}{name}={spec.make()}"
            filled.append(name)
    return "\n".join(lines) + "\n", filled


def _last_env_lines(lines: list[str]) -> dict[str, tuple[int, re.Match[str]]]:
    """Map each variable to its last line, because Compose uses the last line of a name."""
    last: dict[str, tuple[int, re.Match[str]]] = {}
    for i, line in enumerate(lines):
        if match := _ENV_LINE.match(line):
            last[match["name"]] = (i, match)
    return last


def _env_scalar(raw: str) -> str:
    """Return a ``.env`` value as Compose reads it, without a `` #`` comment and surrounding quotes."""
    value = re.split(r"\s#", raw, maxsplit=1)[0].strip()
    if len(value) > 1 and value[0] in "\"'" and value.endswith(value[0]):
        return value[1:-1]
    return value


def write_env(env_path: Path, example_path: Path, specs: tuple[LocalSecret, ...]) -> None:
    """Create ``env_path`` from the example if it is missing, then fill its empty credentials.

    The file is replaced in one step with mode 0600, so a crash cannot leave half a file.

    Args:
        env_path: The Compose ``.env`` file.
        example_path: ``.env.example``, the template for a new file.
        specs: The credentials from ``deploy/secrets.toml``.
    """
    text = env_path.read_text() if env_path.exists() else example_path.read_text()
    new, filled = fill_env(text, specs)
    if env_path.exists() and not filled:
        env_path.chmod(0o600)
        logger.info("%s already holds every credential.", env_path)
        return
    _replace_env(env_path, new)
    logger.info("Generated %s in %s.", ", ".join(filled), env_path)


def _replace_env(env_path: Path, text: str) -> None:
    """Replace ``env_path`` in one step with mode 0600, so a crash cannot leave half a file."""
    fd, tmp = tempfile.mkstemp(dir=env_path.parent, prefix=".env.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    Path(tmp).chmod(0o600)
    Path(tmp).replace(env_path)


def env_value(text: str, name: str) -> str | None:
    """Return the value of the last ``name`` line in ``.env`` text, as Compose reads it."""
    last = _last_env_lines(text.splitlines()).get(name)
    return _env_scalar(last[1]["value"]) if last else None


def set_env(text: str, values: dict[str, str]) -> str:
    """Set each variable on its last line in ``.env`` text, or add it at the end."""
    lines = text.splitlines()
    last = _last_env_lines(lines)
    for name, value in values.items():
        if name in last:
            i, match = last[name]
            lines[i] = f"{match['prefix']}{name}={value}"
        else:
            lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n"


def _kubectl(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        msg = "kubectl not found"
        raise RuntimeError(msg)
    return subprocess.run([kubectl, *args], input=stdin, capture_output=True, text=True, check=False)  # noqa: S603


def _read_secret(namespace: str, name: str) -> dict | None:
    result = _kubectl("get", "secret", name, "--namespace", namespace, "--output", "json")
    if result.returncode != 0:
        if "NotFound" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def secret_manifest(name: str, namespace: str, existing: dict | None, updates: dict[str, str]) -> dict | None:
    """Build the Secret to apply, or ``None`` when there is nothing to change.

    Args:
        name: The Secret name.
        namespace: Its namespace.
        existing: The live Secret, or ``None``.
        updates: Values to set, by key. Every other key keeps its value.

    Returns:
        A Secret manifest with the existing data, the updates and the keep annotation.
    """
    data = dict((existing or {}).get("data") or {})
    annotations = (existing or {}).get("metadata", {}).get("annotations") or {}
    if not updates and annotations.get("helm.sh/resource-policy") == "keep":
        return None
    for key, value in updates.items():
        data[key] = base64.b64encode(value.encode()).decode()
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace, "annotations": _KEEP},
        "type": "Opaque",
        "data": data,
    }


def _apply(manifest: dict) -> None:
    # A client-side apply copies the values into the last-applied annotation.
    result = _kubectl("apply", *APPLY_ARGS, stdin=json.dumps(manifest))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())


def ensure_kind_secrets(namespace: str, specs: tuple[LocalSecret, ...]) -> None:
    """Create every Secret that is missing, and add the keys a Secret lacks.

    Args:
        namespace: The namespace of the release.
        specs: The credentials from ``deploy/secrets.toml``.
    """
    for name in dict.fromkeys(s.secret for s in specs):
        existing = _read_secret(namespace, name)
        have = (existing or {}).get("data") or {}
        missing = {s.key: s.make() for s in specs if s.secret == name and s.key not in have}
        manifest = secret_manifest(name, namespace, existing, missing)
        if manifest is None:
            continue
        _apply(manifest)
        logger.info("Secret %s: generated %s.", name, ", ".join(missing) or "nothing, marked keep")


OIDC_PROJECT = "srdp"
OIDC_APP = "oauth2-proxy"
# oauth2-proxy accepts a user's token as a Bearer token (--skip-jwt-bearer-tokens) only when it is a JWT.
OIDC_ACCESS_FORMAT = "OIDC_TOKEN_TYPE_JWT"
# A hash of the stored client on the oauth2-proxy pods, so a new client restarts them.
OIDC_CLIENT_ANNOTATION = "checksum/oidc-client"
ZITADEL_WAIT_SECONDS = 300

ZitadelApi = Callable[[str, str, dict | None], dict]

# The fields that the oidc_config PUT of the management API accepts.
_OIDC_CONFIG_FIELDS = (
    "responseTypes",
    "grantTypes",
    "appType",
    "authMethodType",
    "postLogoutRedirectUris",
    "version",
    "devMode",
    "accessTokenType",
    "accessTokenRoleAssertion",
    "idTokenRoleAssertion",
    "idTokenUserinfoAssertion",
    "clockSkew",
    "additionalOrigins",
    "skipNativeAppSuccessPage",
    "backChannelLogoutUri",
    "loginVersion",
)


def _zitadel_api(auth_url: str, token: str) -> ZitadelApi:
    """Return a caller for the Zitadel management API that raises on an HTTP error."""

    def call(method: str, path: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(  # noqa: S310  # https URL built from the configured domain
            f"{auth_url}/management/v1/{path}",
            method=method,
            data=json.dumps(body or {}).encode() if method != "GET" else None,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return json.load(response)
        except urllib.error.HTTPError as err:
            msg = f"Zitadel {method} {path}: HTTP {err.code} {err.read().decode(errors='replace')}"
            raise RuntimeError(msg) from err

    return call


def _find_by_name(api: ZitadelApi, path: str, name: str) -> str | None:
    """Return the ID of the first project or app with exactly this name."""
    query = {"queries": [{"nameQuery": {"name": name, "method": "TEXT_QUERY_METHOD_EQUALS"}}]}
    result = api("POST", path, query).get("result") or []
    return result[0]["id"] if result else None


def _ensure_project(api: ZitadelApi) -> str:
    """Return the ID of the SRDP project, created when it is missing."""
    project_id = _find_by_name(api, "projects/_search", OIDC_PROJECT)
    if project_id is None:
        project_id = api("POST", "projects", {"name": OIDC_PROJECT})["id"]
        logger.info("Created Zitadel project %s.", OIDC_PROJECT)
    return project_id


def _ensure_app(api: ZitadelApi, project_id: str) -> tuple[str, str | None]:
    """Return the app ID and, only for a new app, its client secret."""
    app_id = _find_by_name(api, f"projects/{project_id}/apps/_search", OIDC_APP)
    if app_id is not None:
        return app_id, None
    created = api(
        "POST",
        f"projects/{project_id}/apps/oidc",
        {
            "name": OIDC_APP,
            "responseTypes": ["OIDC_RESPONSE_TYPE_CODE"],
            "grantTypes": ["OIDC_GRANT_TYPE_AUTHORIZATION_CODE"],
            "appType": "OIDC_APP_TYPE_WEB",
            "authMethodType": "OIDC_AUTH_METHOD_TYPE_BASIC",
            "accessTokenType": OIDC_ACCESS_FORMAT,
        },
    )
    logger.info("Created Zitadel app %s.", OIDC_APP)
    return created["appId"], created["clientSecret"]


def _sync_app_config(api: ZitadelApi, project_id: str, app_id: str, hosts: list[str]) -> str:
    """Add a callback for every gated host, make the access tokens JWTs, and return the client ID.

    The callbacks the app has stay.
    """
    config = api("GET", f"projects/{project_id}/apps/{app_id}", None)["app"]["oidcConfig"]
    have = set(config.get("redirectUris") or [])
    want = have | {f"https://{host}/oauth2/callback" for host in hosts}
    if want != have or config.get("accessTokenType") != OIDC_ACCESS_FORMAT:
        # The PUT replaces the whole config, so every other setting it accepts goes back unchanged.
        kept = {key: config[key] for key in _OIDC_CONFIG_FIELDS if key in config}
        body = {**kept, "redirectUris": sorted(want), "accessTokenType": OIDC_ACCESS_FORMAT}
        api("PUT", f"projects/{project_id}/apps/{app_id}/oidc_config", body)
        logger.info(
            "Updated the Zitadel app %s, new redirect URIs: %s.", OIDC_APP, ", ".join(sorted(want - have)) or "none"
        )
    return config["clientId"]


def usable_client_id(client_id: str | None, client_secret: str | None) -> str | None:
    """Return the stored client ID when its secret can sign in, or ``None``.

    A missing, empty or ``CHANGE_ME_`` secret cannot, so provisioning then makes a new one.
    """
    if not client_secret or client_secret.startswith("CHANGE_ME_"):
        return None
    return client_id


def provision_oidc(api: ZitadelApi, hosts: list[str], stored_client_id: str | None) -> tuple[str, str] | None:
    """Create or update the oauth2-proxy app in Zitadel.

    Args:
        api: A caller for the Zitadel management API with an admin token.
        hosts: Every hostname behind the login.
        stored_client_id: The client ID that oauth2-proxy has now with a usable secret, if any.

    Returns:
        The client ID and a secret to store, or ``None`` when oauth2-proxy already has this app.
    """
    project_id = _ensure_project(api)
    app_id, secret = _ensure_app(api, project_id)
    client_id = _sync_app_config(api, project_id, app_id, hosts)
    if secret is None and client_id == stored_client_id:
        return None
    if secret is None:
        path = f"projects/{project_id}/apps/{app_id}/oidc_config/_generate_client_secret"
        secret = api("POST", path, {})["clientSecret"]
    return client_id, secret


def _secret_value(namespace: str, name: str, key: str) -> str | None:
    """Return one decoded value of a Secret, or ``None`` when the Secret or key is missing."""
    existing = _read_secret(namespace, name)
    value = (existing or {}).get("data", {}).get(key)
    return base64.b64decode(value).decode() if value else None


def _gated_hosts(namespace: str) -> list[str]:
    """Return the hosts of the chart's oauth2-proxy routes, the hosts behind the login."""
    result = _kubectl("get", "ingress", "auth-routes-ingress", "--namespace", namespace, "--output", "json")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return [rule["host"] for rule in json.loads(result.stdout)["spec"]["rules"]]


def _zitadel_ready(auth_url: str) -> bool:
    """Return whether Zitadel answers its health check, which it does only after its setup."""
    try:
        with urllib.request.urlopen(f"{auth_url}/debug/healthz", timeout=10) as response:  # noqa: S310
            return response.status == 200  # noqa: PLR2004
    except ssl.SSLCertVerificationError:
        raise
    except OSError:
        return False


def _zitadel_admin(read_token: Callable[[], str | None], missing: str, domain: str) -> ZitadelApi:
    """Return the management API with the admin token, once Zitadel accepts it.

    The token is read again on every try, because Zitadel replaces it when it starts on a new database.
    A TLS error stops at once, because waiting does not fix a missing CA.
    """
    auth_url = f"https://auth.{domain}"
    deadline = time.monotonic() + ZITADEL_WAIT_SECONDS
    while True:
        token = (read_token() or "").strip()
        if token:
            api = _zitadel_api(auth_url, token)
            try:
                api("GET", "orgs/me", None)
            except ssl.SSLCertVerificationError:
                raise
            except (RuntimeError, OSError) as err:
                problem = f"Zitadel did not accept the admin token: {err}"
            else:
                return api
        elif _zitadel_ready(auth_url):
            # Zitadel writes the token during its setup, so a running Zitadel without it never writes it.
            raise RuntimeError(missing)
        else:
            problem = missing
        if time.monotonic() > deadline:
            raise RuntimeError(problem)
        time.sleep(5)


def compose_gated_hosts(routes: str, domain: str) -> list[str]:
    """Return the hosts of the Compose oauth2-proxy router, the hosts behind the login, without Zitadel's."""
    rule = re.search(r"^ {4}oauth2-proxy:\n(?: {6}.*\n)*? {6}rule: (.*)$", routes, re.MULTILINE)
    if rule is None:
        msg = "no oauth2-proxy router rule in the Traefik routes"
        raise RuntimeError(msg)
    hosts = [h.replace('{{ env "SRDP_DOMAIN" }}', domain) for h in re.findall(r"Host\(`([^`]*)`\)", rule[1])]
    return [h for h in hosts if h != f"auth.{domain}"]


def _read_private(path: Path) -> str | None:
    """Return the content of a token file and make it readable only by the user, or ``None`` when it is missing."""
    if not path.exists():
        return None
    path.chmod(0o600)
    return path.read_text()


def ensure_compose_oidc(env_path: Path, domain: str) -> None:
    """Create or update the oauth2-proxy app and save its client in ``.env``.

    Args:
        env_path: The Compose ``.env`` file. Zitadel writes ``admin.pat`` to ``deploy/zitadel-admin`` next to its folder.
        domain: The base domain from ``srdp.toml``, so Zitadel is ``auth.<domain>``.
    """
    # Outside the Compose folder, because zitadel-login mounts that folder and file modes do not hold on every Docker host.
    pat = env_path.parent.parent / "zitadel-admin" / "admin.pat"
    missing = f"{pat} not found. Zitadel writes it only on its first start, so start a new stack."
    api = _zitadel_admin(lambda: _read_private(pat), missing, domain)
    text = env_path.read_text()
    hosts = compose_gated_hosts(TRAEFIK_ROUTES.read_text(), domain)
    stored = usable_client_id(env_value(text, "OAUTH2_PROXY_CLIENT_ID"), env_value(text, "OAUTH2_PROXY_CLIENT_SECRET"))
    client = provision_oidc(api, hosts, stored)
    if client is None:
        logger.info("oauth2-proxy already has the Zitadel app.")
        return
    client_id, client_secret = client
    _replace_env(
        env_path, set_env(text, {"OAUTH2_PROXY_CLIENT_ID": client_id, "OAUTH2_PROXY_CLIENT_SECRET": client_secret})
    )
    logger.info("Saved the OIDC client in %s.", env_path)


def ensure_kind_oidc(namespace: str, domain: str) -> None:
    """Create or update the oauth2-proxy app, store its client in ``srdp-oauth2-proxy`` and restart oauth2-proxy.

    Args:
        namespace: The namespace of the release.
        domain: The base domain from ``srdp.toml``, so Zitadel is ``auth.<domain>``.
    """
    # Check where the client goes first, because a new secret in Zitadel invalidates the old one.
    if _read_secret(namespace, "srdp-oauth2-proxy") is None:
        msg = "Secret srdp-oauth2-proxy not found, run `just local-deploy` first"
        raise RuntimeError(msg)
    api = _zitadel_admin(
        lambda: _secret_value(namespace, "iam-admin-pat", "pat"),
        "Secret iam-admin-pat not found, is Zitadel deployed?",
        domain,
    )
    client_id = _secret_value(namespace, "srdp-oauth2-proxy", "client-id")
    client_secret = _secret_value(namespace, "srdp-oauth2-proxy", "client-secret")
    client = provision_oidc(api, _gated_hosts(namespace), usable_client_id(client_id, client_secret))
    if client is None and client_id and client_secret:
        logger.info("oauth2-proxy already has the Zitadel app.")
        client = (client_id, client_secret)
    elif client is not None:
        store_oidc_client(namespace, *client)
    if client is not None:
        roll_oauth2_proxy(namespace, *client)


def store_oidc_client(namespace: str, client_id: str, client_secret: str) -> None:
    """Put the OIDC client into ``srdp-oauth2-proxy``.

    Args:
        namespace: The namespace of the release.
        client_id: The client ID of the Zitadel app.
        client_secret: The client secret of the Zitadel app.
    """
    name = "srdp-oauth2-proxy"
    existing = _read_secret(namespace, name)
    if existing is None:
        msg = f"Secret {name} not found, run `just local-deploy` first"
        raise RuntimeError(msg)
    manifest = secret_manifest(name, namespace, existing, {"client-id": client_id, "client-secret": client_secret})
    if manifest is not None:
        _apply(manifest)
        logger.info("Stored the OIDC client in %s.", name)


def roll_oauth2_proxy(namespace: str, client_id: str, client_secret: str) -> None:
    """Restart oauth2-proxy once for each OIDC client, so a rerun retries a restart that failed.

    The pod template carries a hash of the client. The same hash changes nothing, a new one rolls the pods.

    Args:
        namespace: The namespace of the release.
        client_id: The client ID in ``srdp-oauth2-proxy``.
        client_secret: The client secret in ``srdp-oauth2-proxy``.
    """
    digest = hashlib.sha256(f"{client_id}:{client_secret}".encode()).hexdigest()[:16]
    patch = {"spec": {"template": {"metadata": {"annotations": {OIDC_CLIENT_ANNOTATION: digest}}}}}
    result = _kubectl(
        "patch",
        "deployment",
        "srdp-oauth2-proxy",
        "--namespace",
        namespace,
        "--type",
        "merge",
        "--patch",
        json.dumps(patch),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    logger.info("oauth2-proxy runs with the stored OIDC client.")


def main(argv: list[str]) -> None:
    """Run one step for a local stack, as the usage message lists."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    match argv:
        case ["compose", env_path, example_path]:
            write_env(Path(env_path), Path(example_path), load_secrets())
        case ["kind", namespace]:
            ensure_kind_secrets(namespace, load_secrets())
        case ["oidc-compose", env_path, domain]:
            ensure_compose_oidc(Path(env_path), domain)
        case ["oidc-kind", namespace, domain]:
            ensure_kind_oidc(namespace, domain)
        case _:
            sys.exit(
                "usage: local_secrets.py compose <env> <example> | kind <namespace>"
                " | oidc-compose <env> <domain> | oidc-kind <namespace> <domain>"
            )


if __name__ == "__main__":
    main(sys.argv[1:])
