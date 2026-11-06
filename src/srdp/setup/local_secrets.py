"""Generate the credentials of a local stack, once, so no password lives in a tracked file.

Compose reads them from ``deploy/docker/.env``, and kind from Secrets with the fixed names that an
operator creates on any other cluster. ``deploy/secrets.toml`` is the one list of both. A value that
exists is never replaced, so a redeploy keeps the passwords that the databases already hold.

Run through ``just``: ``docker-secrets``, ``local-secrets`` and ``local-oidc-client``.
"""

import base64
import getpass
import json
import logging
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
import tomllib
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


SECRETS_FILE = Path(__file__).resolve().parents[3] / "deploy" / "secrets.toml"


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


# ---------------------------------------------------------------------------
# Compose: deploy/docker/.env
# ---------------------------------------------------------------------------


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
    filled: list[str] = []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        name, sep, rest = line.partition("=")
        name = name.strip()
        if not sep or name not in wanted:
            continue
        seen.add(name)
        # Compose reads " #" as the start of a comment, and a "#" inside a value as part of it.
        if not re.split(r"\s#", rest, maxsplit=1)[0].strip():
            lines[i] = f"{name}={wanted[name].make()}"
            filled.append(name)
    for name, spec in wanted.items():
        if name not in seen:
            lines.append(f"{name}={spec.make()}")
            filled.append(name)
    return "\n".join(lines) + "\n", filled


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
        logger.info("%s already holds every credential.", env_path)
        return
    fd, tmp = tempfile.mkstemp(dir=env_path.parent, prefix=".env.")
    with os.fdopen(fd, "w") as f:
        f.write(new)
    Path(tmp).chmod(0o600)
    Path(tmp).replace(env_path)
    logger.info("Generated %s in %s.", ", ".join(filled), env_path)


# ---------------------------------------------------------------------------
# kind: Secrets in the cluster
# ---------------------------------------------------------------------------


def _kubectl(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        msg = "kubectl not found"
        raise RuntimeError(msg)
    return subprocess.run([kubectl, *args], input=stdin, capture_output=True, text=True, check=False)  # noqa: S603 — fixed argv, values go through stdin


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
    result = _kubectl("apply", "--filename", "-", stdin=json.dumps(manifest))
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


def store_oidc_client(namespace: str, client_id: str, client_secret: str) -> None:
    """Put the OIDC client that Zitadel created into ``srdp-oauth2-proxy`` and restart oauth2-proxy.

    Args:
        namespace: The namespace of the release.
        client_id: The client ID from the Zitadel console.
        client_secret: The client secret from the Zitadel console.
    """
    name = "srdp-oauth2-proxy"
    existing = _read_secret(namespace, name)
    if existing is None:
        msg = f"Secret {name} not found, run `just local-deploy` first"
        raise RuntimeError(msg)
    manifest = secret_manifest(name, namespace, existing, {"client-id": client_id, "client-secret": client_secret})
    if manifest is not None:
        _apply(manifest)
    result = _kubectl("rollout", "restart", "deployment/srdp-oauth2-proxy", "--namespace", namespace)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    logger.info("Stored the OIDC client and restarted oauth2-proxy.")


def main(argv: list[str]) -> None:
    """Run ``compose <env> <example>``, ``kind <namespace>`` or ``oidc <namespace>``."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    match argv:
        case ["compose", env_path, example_path]:
            write_env(Path(env_path), Path(example_path), load_secrets())
        case ["kind", namespace]:
            ensure_kind_secrets(namespace, load_secrets())
        case ["oidc", namespace]:
            client_id = input("Client ID: ").strip()
            client_secret = getpass.getpass("Client secret: ").strip()
            if not client_id or not client_secret:
                sys.exit("Both the client ID and the client secret are needed.")
            store_oidc_client(namespace, client_id, client_secret)
        case _:
            sys.exit("usage: local_secrets.py compose <env> <example> | kind <namespace> | oidc <namespace>")


if __name__ == "__main__":
    main(sys.argv[1:])
