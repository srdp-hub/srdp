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
    assert env["POSTGRES_PASSWORD"] == "mine"  # noqa: S105 — throwaway test value
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


def test_hash_inside_a_value_counts_as_set() -> None:
    """Compose keeps a "#" that follows no space, so such a value is not empty."""
    env = _env(fill_env("POSTGRES_PASSWORD=#abc\nMARQUEZ_DB_PASSWORD= # empty\n", SECRETS)[0])
    assert env["POSTGRES_PASSWORD"] == "#abc"  # noqa: S105 — throwaway test value
    assert env["MARQUEZ_DB_PASSWORD"]
