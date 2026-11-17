"""Tests for the generated credentials of the local stacks."""

import base64
import re
import stat
from pathlib import Path

import pytest

from srdp.setup import local_secrets
from srdp.setup.local_secrets import LocalSecret, fill_env, load_secrets, secret_manifest, write_env

SECRETS = load_secrets()

DOCKER = Path(__file__).resolve().parents[2] / "deploy" / "docker"
EXAMPLE = DOCKER / ".env.example"


def _env(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))


def test_compose_reads_only_listed_credentials() -> None:
    """Every password, secret, token or key that Compose reads is in deploy/secrets.toml."""
    text = (DOCKER / "docker-compose.yml").read_text()
    names = set(re.findall(r"\$\{([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|MASTERKEY|KEY_ID))\b", text))
    assert names <= {s.env for s in SECRETS}, names - {s.env for s in SECRETS}


@pytest.mark.parametrize("fmt", ["alnum", "alnum:0", "hex:x", "base64:32", "placeholder:3"])
def test_unknown_format_is_refused(tmp_path: Path, fmt: str) -> None:
    """A typo in a format stops the run before anything is generated."""
    path = tmp_path / "secrets.toml"
    path.write_text(f'[[secret]]\nsecret = "s"\nkey = "k"\nformat = "{fmt}"\n')

    with pytest.raises(ValueError, match="Unknown format"):
        load_secrets(path)


def test_duplicate_variable_is_refused(tmp_path: Path) -> None:
    """Two entries for one variable would overwrite each other."""
    entry = '[[secret]]\nsecret = "{name}"\nkey = "{key}"\nformat = "hex:8"\n'
    path = tmp_path / "secrets.toml"
    path.write_text(entry.format(name="srdp-a-b", key="c") + entry.format(name="srdp-a", key="b-c"))

    with pytest.raises(ValueError, match="twice"):
        load_secrets(path)


def test_variable_follows_secret_and_key() -> None:
    """The .env name is the Secret without srdp- plus the key, so both targets use one name."""
    assert LocalSecret("srdp-ducklake-s3-writer", "key-id", "hex:8").env == "DUCKLAKE_S3_WRITER_KEY_ID"


def test_placeholder_names_its_variable() -> None:
    """A placeholder says which value the user still has to set."""
    spec = LocalSecret("srdp-oauth2-proxy", "client-id", "placeholder")
    assert spec.make() == "CHANGE_ME_OAUTH2_PROXY_CLIENT_ID"


def test_example_lists_every_credential() -> None:
    """Every generated variable has its line in .env.example, so Compose and the docs know it."""
    names = set(_env(EXAMPLE.read_text()))
    assert {s.env for s in SECRETS} <= names


def test_empty_values_are_filled() -> None:
    """A new .env from the example gets a value for every credential."""
    env = _env(fill_env(EXAMPLE.read_text(), SECRETS)[0])
    for spec in SECRETS:
        assert env[spec.env].split("#")[0].strip(), spec.env


def test_set_values_stay() -> None:
    """A value the user set, or a placeholder, is never replaced."""
    text = "POSTGRES_PASSWORD=mine\nOAUTH2_PROXY_CLIENT_ID=CHANGE_ME_OAUTH2_PROXY_CLIENT_ID # comment\n"
    env = _env(fill_env(text, SECRETS)[0])
    assert env["POSTGRES_PASSWORD"] == "mine"  # noqa: S105  # throwaway test value
    assert env["OAUTH2_PROXY_CLIENT_ID"].startswith("CHANGE_ME_OAUTH2_PROXY_CLIENT_ID")


def test_other_empty_values_stay_empty() -> None:
    """Only credentials are generated, so ACME_EMAIL stays for the user to fill."""
    env = _env(fill_env("ACME_EMAIL=\n", SECRETS)[0])
    assert env["ACME_EMAIL"] == ""


def test_missing_credentials_are_appended() -> None:
    """An older .env without the catalog roles gets them added."""
    new, filled = fill_env("POSTGRES_PASSWORD=mine\n", SECRETS)
    assert "DUCKLAKE_PG_READER_PASSWORD" in filled
    assert _env(new)["DUCKLAKE_PG_READER_PASSWORD"]


def test_values_have_the_formats_the_services_need() -> None:
    """Zitadel and Garage reject values in another format."""
    values = {s.env: s.make() for s in SECRETS}
    assert len(values["ZITADEL_MASTERKEY"]) == 32
    assert len(values["OAUTH2_PROXY_COOKIE_SECRET"]) == 32
    assert re.fullmatch(r"GK[0-9a-f]{24}", values["DUCKLAKE_S3_WRITER_KEY_ID"])
    assert re.fullmatch(r"[0-9a-f]{64}", values["GARAGE_RPC_SECRET"])
    admin = values["ZITADEL_ADMIN_PASSWORD"]
    for pattern in ("[A-Z]", "[a-z]", "[0-9]", "[^A-Za-z0-9]"):
        assert re.search(pattern, admin), pattern


def test_values_differ_per_run() -> None:
    """Two stacks never share a generated password."""
    assert SECRETS[0].make() != SECRETS[0].make()


def test_new_env_file_is_private(tmp_path: Path) -> None:
    """The new .env is readable by its owner only."""
    env = tmp_path / ".env"

    write_env(env, EXAMPLE, SECRETS)

    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert _env(env.read_text())["POSTGRES_PASSWORD"]


def test_complete_env_file_is_made_private(tmp_path: Path) -> None:
    """A complete .env that was copied by hand is readable by its owner only after a run."""
    env = tmp_path / ".env"
    write_env(env, EXAMPLE, SECRETS)
    env.chmod(0o644)

    write_env(env, EXAMPLE, SECRETS)

    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_complete_env_file_is_left_alone(tmp_path: Path) -> None:
    """A second run changes nothing."""
    env = tmp_path / ".env"
    write_env(env, EXAMPLE, SECRETS)
    before = env.read_text()

    write_env(env, EXAMPLE, SECRETS)

    assert env.read_text() == before


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def test_new_secret_is_marked_keep() -> None:
    """Helm must not delete a Secret that an older chart rendered."""
    manifest = secret_manifest("srdp-marquez", "srdp", None, {"db-password": "pw"})

    assert manifest is not None
    assert manifest["metadata"]["annotations"] == {"helm.sh/resource-policy": "keep"}
    assert manifest["data"] == {"db-password": _b64("pw")}


def test_existing_values_are_kept() -> None:
    """An older Secret keeps its values and only gets the keys it lacks."""
    existing = {"metadata": {"annotations": {}}, "data": {"masterkey": _b64("old")}}

    manifest = secret_manifest("srdp-zitadel", "srdp", existing, {"db-password": "new"})

    assert manifest is not None
    assert manifest["data"] == {"masterkey": _b64("old"), "db-password": _b64("new")}


def test_complete_kept_secret_is_skipped() -> None:
    """Nothing is applied when every key is there and the Secret is marked keep."""
    existing = {"metadata": {"annotations": {"helm.sh/resource-policy": "keep"}}, "data": {"x": _b64("y")}}

    assert secret_manifest("srdp-marquez", "srdp", existing, {}) is None


def test_kind_run_generates_only_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second deploy reads every Secret back and applies nothing."""
    store: dict[str, dict] = {}
    monkeypatch.setattr(local_secrets, "_read_secret", lambda _ns, name: store.get(name))
    monkeypatch.setattr(local_secrets, "_apply", lambda m: store.__setitem__(m["metadata"]["name"], m))

    local_secrets.ensure_kind_secrets("srdp", SECRETS)
    first = {name: dict(m["data"]) for name, m in store.items()}
    monkeypatch.setattr(local_secrets, "_apply", lambda _m: pytest.fail("applied again"))
    local_secrets.ensure_kind_secrets("srdp", SECRETS)

    assert {(name, key) for name, data in first.items() for key in data} == {(s.secret, s.key) for s in SECRETS}


def test_secrets_are_applied_server_side(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client-side apply would copy every value into the last-applied annotation."""
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(local_secrets, "_kubectl", lambda *a, **_k: calls.append(a) or _Done())

    local_secrets._apply({"kind": "Secret"})

    assert "--server-side" in calls[0]


class _Done:
    returncode = 0
    stderr = ""


def test_export_prefix_keeps_the_value() -> None:
    """Compose reads "export NAME=value", so that value stays and is not added twice."""
    new, filled = fill_env("export POSTGRES_PASSWORD=mine\nexport MARQUEZ_DB_PASSWORD=\n", SECRETS)
    assert "POSTGRES_PASSWORD" not in filled
    assert "\nPOSTGRES_PASSWORD=" not in new
    assert re.search(r"^export MARQUEZ_DB_PASSWORD=\S+$", new, re.MULTILINE)


def test_export_with_more_spaces_keeps_the_value() -> None:
    """Compose accepts any whitespace after "export", so that value stays."""
    new, filled = fill_env("export  POSTGRES_PASSWORD=mine\n", SECRETS)
    assert "POSTGRES_PASSWORD" not in filled
    assert new.count("POSTGRES_PASSWORD") == 1


def test_only_the_last_duplicate_counts() -> None:
    """Compose uses the last line of a name, so an empty earlier line stays and a set last line wins."""
    new, filled = fill_env(
        "POSTGRES_PASSWORD=\nPOSTGRES_PASSWORD=mine\nMARQUEZ_DB_PASSWORD=x\nMARQUEZ_DB_PASSWORD=\n", SECRETS
    )
    assert "POSTGRES_PASSWORD" not in filled
    assert new.startswith("POSTGRES_PASSWORD=\nPOSTGRES_PASSWORD=mine\nMARQUEZ_DB_PASSWORD=x\nMARQUEZ_DB_PASSWORD=")
    assert filled.count("MARQUEZ_DB_PASSWORD") == 1
    assert _env(new)["MARQUEZ_DB_PASSWORD"]


@pytest.mark.parametrize("empty", ['""', "''"])
def test_quoted_empty_value_is_filled(empty: str) -> None:
    """Compose reads a quoted empty value as empty, so it gets a generated value."""
    env = _env(fill_env(f"POSTGRES_PASSWORD={empty}\n", SECRETS)[0])
    assert env["POSTGRES_PASSWORD"] not in {"", '""', "''"}


def test_hash_inside_a_value_counts_as_set() -> None:
    """Compose keeps a "#" that follows no space, so such a value is not empty."""
    env = _env(fill_env("POSTGRES_PASSWORD=#abc\nMARQUEZ_DB_PASSWORD= # empty\n", SECRETS)[0])
    assert env["POSTGRES_PASSWORD"] == "#abc"  # noqa: S105  # throwaway test value
    assert env["MARQUEZ_DB_PASSWORD"]


class FakeZitadel:
    """Answers the management API calls of ``provision_oidc`` from an in-memory project and app."""

    def __init__(  # noqa: D107
        self, *, app: bool, redirect_uris: tuple[str, ...] = (), access_format: str = "OIDC_TOKEN_TYPE_JWT"
    ) -> None:
        self.app = app
        self.redirect_uris = list(redirect_uris)
        self.access_format = access_format
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body: dict | None) -> dict:
        """Record the call and answer it."""
        self.calls.append((method, path))
        if path == "projects/_search":
            return {"result": [{"id": "p1"}]}
        if path == "projects/p1/apps/_search":
            return {"result": [{"id": "a1"}]} if self.app else {}
        if path == "projects/p1/apps/oidc":
            self.app = True
            self.access_format = (body or {})["accessTokenType"]
            return {"appId": "a1", "clientId": "c1", "clientSecret": "new-app-secret"}
        if method == "GET":
            config = {
                "clientId": "c1",
                "redirectUris": self.redirect_uris,
                "accessTokenType": self.access_format,
                "complianceProblems": [],
            }
            return {"app": {"oidcConfig": config}}
        if path.endswith("/oidc_config"):
            self.put = body or {}
            self.redirect_uris = self.put["redirectUris"]
            self.access_format = self.put["accessTokenType"]
            return {}
        if path.endswith("/_generate_client_secret"):
            return {"clientSecret": "regenerated"}
        raise AssertionError(path)


HOSTS = ["srdp.localhost", "api.srdp.localhost"]
CALLBACKS = ["https://api.srdp.localhost/oauth2/callback", "https://srdp.localhost/oauth2/callback"]


def test_new_app_gets_every_callback() -> None:
    """A fresh Zitadel gets the app with a callback per gated host, and oauth2-proxy its first secret."""
    zitadel = FakeZitadel(app=False)

    assert local_secrets.provision_oidc(zitadel, HOSTS, "CHANGE_ME_OAUTH2_PROXY_CLIENT_ID") == ("c1", "new-app-secret")
    assert zitadel.redirect_uris == CALLBACKS
    assert zitadel.access_format == "OIDC_TOKEN_TYPE_JWT"


def test_opaque_tokens_become_jwts() -> None:
    """An app with opaque access tokens and every callback gets JWT access tokens."""
    zitadel = FakeZitadel(app=True, redirect_uris=tuple(CALLBACKS), access_format="OIDC_TOKEN_TYPE_BEARER")

    assert local_secrets.provision_oidc(zitadel, HOSTS, "c1") is None
    assert zitadel.access_format == "OIDC_TOKEN_TYPE_JWT"
    assert zitadel.redirect_uris == CALLBACKS


def test_rerun_changes_nothing() -> None:
    """A second run finds the app and the stored client, and changes neither."""
    zitadel = FakeZitadel(app=True, redirect_uris=tuple(CALLBACKS))

    assert local_secrets.provision_oidc(zitadel, HOSTS, "c1") is None
    assert all(method == "GET" or path.endswith("_search") for method, path in zitadel.calls)


def test_other_client_gets_a_new_secret() -> None:
    """oauth2-proxy with another client ID gets this app's ID and a new secret, and extra URIs stay."""
    zitadel = FakeZitadel(app=True, redirect_uris=("https://own.example/oauth2/callback",))

    assert local_secrets.provision_oidc(zitadel, HOSTS, "CHANGE_ME_OAUTH2_PROXY_CLIENT_ID") == ("c1", "regenerated")
    assert zitadel.redirect_uris == sorted([*CALLBACKS, "https://own.example/oauth2/callback"])


def test_sync_keeps_the_app_settings() -> None:
    """Adding a callback sends the other settings back, so the PUT resets none of them."""
    zitadel = FakeZitadel(app=True)

    local_secrets.provision_oidc(zitadel, HOSTS, "c1")

    assert zitadel.put["accessTokenType"] == "OIDC_TOKEN_TYPE_JWT"
    assert "complianceProblems" not in zitadel.put
    assert "clientId" not in zitadel.put


def test_quoted_client_id_counts_as_set() -> None:
    """Compose strips quotes, so a quoted client ID matches the app and gets no new secret."""
    text = "OAUTH2_PROXY_CLIENT_ID=\"c1\" # from Zitadel\nOAUTH2_PROXY_CLIENT_SECRET=''\n"

    assert local_secrets.env_value(text, "OAUTH2_PROXY_CLIENT_ID") == "c1"
    assert local_secrets.env_value(text, "OAUTH2_PROXY_CLIENT_SECRET") == ""


def test_new_token_on_disk_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale token is read again on the next try, so the new token of a reset Zitadel is used."""
    tokens = iter(["stale", "fresh"])
    seen: list[str] = []

    def api_for(_url: str, token: str) -> local_secrets.ZitadelApi:
        def call(_method: str, _path: str, _body: dict | None) -> dict:
            seen.append(token)
            if token == "stale":  # noqa: S105  # test value
                msg = "HTTP 401"
                raise RuntimeError(msg)
            return {}

        return call

    monkeypatch.setattr(local_secrets, "_zitadel_api", api_for)
    monkeypatch.setattr(local_secrets.time, "sleep", lambda _s: None)

    local_secrets._zitadel_admin(lambda: next(tokens), "missing", "example.test")

    assert seen == ["stale", "fresh"]


def test_running_zitadel_without_token_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zitadel writes the token during setup, so a healthy Zitadel without it stops the step at once."""
    monkeypatch.setattr(local_secrets, "_zitadel_ready", lambda _url: True)
    monkeypatch.setattr(local_secrets.time, "sleep", lambda _s: pytest.fail("waited"))

    with pytest.raises(RuntimeError, match="start a new stack"):
        local_secrets._zitadel_admin(lambda: None, "admin.pat not found, start a new stack", "example.test")


def test_compose_hosts_come_from_the_routes() -> None:
    """The Compose login gate lists the same hosts as the chart's auth Ingress, without Zitadel's."""
    routes = local_secrets.TRAEFIK_ROUTES.read_text()

    hosts = local_secrets.compose_gated_hosts(routes, "example.test")

    assert "api.example.test" in hosts
    assert "example.test" in hosts
    assert "auth.example.test" not in hosts


def test_set_env_keeps_the_rest() -> None:
    """The client replaces the last line of its name, and every other line stays."""
    text = "A=1\nOAUTH2_PROXY_CLIENT_ID=old\n# note\nOAUTH2_PROXY_CLIENT_ID=older # x\n"

    new = local_secrets.set_env(text, {"OAUTH2_PROXY_CLIENT_ID": "c1", "OAUTH2_PROXY_CLIENT_SECRET": "s1"})

    assert local_secrets.env_value(text, "OAUTH2_PROXY_CLIENT_ID") == "older"
    assert new == "A=1\nOAUTH2_PROXY_CLIENT_ID=old\n# note\nOAUTH2_PROXY_CLIENT_ID=c1\nOAUTH2_PROXY_CLIENT_SECRET=s1\n"
