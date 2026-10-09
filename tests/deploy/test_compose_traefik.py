"""Check the Docker Compose Traefik setup: the domain from srdp.toml, the routing files and the production config."""

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKER_DIR = REPO_ROOT / "deploy" / "docker"
TRAEFIK_DIR = REPO_ROOT / "config" / "traefik"
DOMAIN = tomllib.loads((REPO_ROOT / "srdp.toml").read_text())["deploy"]["domain"]


def compose(name: str) -> dict[str, Any]:
    """Load a compose file from deploy/docker without interpolating it."""
    return yaml.safe_load((DOCKER_DIR / name).read_text())


def test_compose_falls_back_to_the_srdp_toml_domain() -> None:
    """`docker compose` without the Justfile (no SRDP_DOMAIN) must land on the same domain."""
    text = (DOCKER_DIR / "docker-compose.yml").read_text()
    defaults = re.findall(r"\$\{SRDP_DOMAIN:-([^}]*)\}", text)
    assert defaults
    assert set(defaults) == {DOMAIN}
    without_defaults = re.sub(r"\$\{SRDP_DOMAIN:-[^}]*\}", "", text)
    assert DOMAIN not in without_defaults


def test_routing_takes_every_hostname_from_srdp_domain() -> None:
    text = (TRAEFIK_DIR / "dynamic" / "srdp.yml").read_text()
    assert DOMAIN not in text
    hosts = re.findall(r"Host\(`([^`]*)`\)", text)
    assert hosts
    assert all(host.endswith('{{ env "SRDP_DOMAIN" }}') for host in hosts), hosts


def test_no_router_replaces_the_entrypoint_tls() -> None:
    """A router-level tls block would drop the production certificate resolver set on the entrypoint."""
    routers = yaml.safe_load((TRAEFIK_DIR / "dynamic" / "srdp.yml").read_text())["http"]["routers"]
    assert [name for name, router in routers.items() if "tls" in router] == []


def test_routing_files_are_mounted_one_by_one() -> None:
    """A read-only directory mount would stop an overlay from adding its own file to /etc/traefik/dynamic."""
    for name in ("docker-compose.yml", "docker-compose.override.yml"):
        volumes = compose(name)["services"]["traefik"]["volumes"]
        targets = [volume.split(":")[1] for volume in volumes]
        assert "/etc/traefik/dynamic" not in targets
        assert "/etc/traefik/dynamic/" not in targets


def test_dashboard_port_is_published_only_for_local_development() -> None:
    assert "8080:8080" not in compose("docker-compose.yml")["services"]["traefik"]["ports"]
    assert "8080:8080" in compose("docker-compose.override.yml")["services"]["traefik"]["ports"]
    assert "ports" not in compose("docker-compose.prod.yml")["services"]["traefik"]


def test_local_certificate_is_loaded_only_for_local_development() -> None:
    """Production has no mkcert certificate, and a missing default certificate breaks every TLS handshake."""
    assert "tls" not in yaml.safe_load((TRAEFIK_DIR / "dynamic" / "srdp.yml").read_text())
    assert "tls" not in yaml.safe_load((TRAEFIK_DIR / "traefik.yml").read_text())
    override = compose("docker-compose.override.yml")["services"]["traefik"]["volumes"]
    assert any(volume.endswith("/etc/traefik/dynamic/local-tls.yml:ro") for volume in override)


def test_production_traefik_uses_its_own_static_config() -> None:
    """Traefik reads one static config source, so production replaces the file instead of adding flags."""
    prod = compose("docker-compose.prod.yml")
    traefik = prod["services"]["traefik"]
    (mount,) = traefik["configs"]
    assert traefik["command"] == [f"--configFile={mount['target']}"]
    assert f"--configFile={mount['target']}" in traefik["healthcheck"]["test"]

    static = yaml.safe_load(prod["configs"][mount["source"]]["content"])
    assert "api" not in static
    resolver = static["entrypoints"]["websecure"]["http"]["tls"]["certResolver"]
    assert resolver in static["certificatesResolvers"]
    assert static["certificatesResolvers"][resolver]["acme"]["httpChallenge"]["entryPoint"] == "web"
    # Production loads the same routing files as local development.
    assert static["providers"] == yaml.safe_load((TRAEFIK_DIR / "traefik.yml").read_text())["providers"]
