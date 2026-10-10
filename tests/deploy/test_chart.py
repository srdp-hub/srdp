"""Render the Helm chart with `helm template` and check its contract with the Compose stack."""

import re
import shlex
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_DIR = REPO_ROOT / "deploy" / "kubernetes" / "srdp-chart"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="needs helm")

Manifest = dict[str, Any]


@pytest.fixture(scope="module", autouse=True)
def _chart_dependencies() -> None:
    """Fail rather than skip without the subcharts, so CI cannot pass by testing nothing."""
    if not (CHART_DIR / "charts").is_dir():
        pytest.fail("chart dependencies missing, run `just chart-deps`")


def render(*values_files: str, set_values: tuple[str, ...] = ()) -> list[Manifest]:
    """Render the chart with the given values files (relative to the chart) and --set overrides."""
    args = ["helm", "template", "srdp", str(CHART_DIR), "--namespace", "srdp"]
    for values_file in values_files:
        args += ["-f", str(CHART_DIR / values_file)]
    for set_value in set_values:
        args += ["--set", set_value]
    # Fixed argv, no shell: every argument comes from this test module.
    result = subprocess.run(args, capture_output=True, text=True, check=True)  # noqa: S603
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def find(manifests: list[Manifest], kind: str, name: str) -> Manifest:
    """Return the single manifest with this kind and name."""
    matches = [m for m in manifests if m["kind"] == kind and m["metadata"]["name"] == name]
    assert len(matches) == 1, f"expected one {kind}/{name}, found {len(matches)}"
    return matches[0]


def pod_specs(manifests: list[Manifest]) -> list[tuple[str, Manifest]]:
    """Return (name, pod spec) for every Deployment and Job."""
    return [
        (m["metadata"]["name"], m["spec"]["template"]["spec"]) for m in manifests if m["kind"] in {"Deployment", "Job"}
    ]


def containers(spec: Manifest) -> list[Manifest]:
    """Return the init containers and containers of a pod spec."""
    return spec.get("initContainers", []) + spec.get("containers", [])


@pytest.fixture(scope="module")
def local() -> list[Manifest]:
    """Render the chart the way `just local-deploy` does."""
    return render("values.yaml", "values-local.yaml")


def test_streamlit_runs_behind_the_login(local: list[Manifest]) -> None:
    deployment = find(local, "Deployment", "streamlit")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"].endswith("/streamlit:dev")
    assert find(local, "Service", "streamlit")["spec"]["ports"][0]["targetPort"] == 8000

    ingress = find(local, "Ingress", "streamlit-ingress")
    assert ingress["spec"]["rules"][0]["host"] == "streamlit.srdp.localhost"
    assert "oauth-auth" in ingress["metadata"]["annotations"]["traefik.ingress.kubernetes.io/router.middlewares"]
    auth_hosts = [r["host"] for r in find(local, "Ingress", "auth-routes-ingress")["spec"]["rules"]]
    assert "streamlit.srdp.localhost" in auth_hosts


def test_setup_job_covers_all_four_databases(local: list[Manifest]) -> None:
    toml = find(local, "ConfigMap", "srdp-setup-config")["data"]["srdp.toml"]
    for database in ("zitadel", "dagster", "marquez", "ducklake"):
        assert f'name = "{database}"' in toml


def test_dagster_code_location_uses_the_compose_module_path(local: list[Manifest]) -> None:
    deployment = find(local, "Deployment", "srdp-dagster-user-deployments-srdp-etl")
    args = deployment["spec"]["template"]["spec"]["containers"][0]["args"]
    assert args[args.index("-m") + 1] == "etl.definitions"


def test_compose_and_chart_queue_runs_the_same_way(local: list[Manifest]) -> None:
    """At most 3 runs at once, one of them a backfill, on both deployment targets."""
    instance = yaml.safe_load(find(local, "ConfigMap", "srdp-dagster-instance")["data"]["dagster.yaml"])
    compose = yaml.safe_load((REPO_ROOT / "config" / "dagster" / "dagster.yaml").read_text())
    backfills = [{"key": "workload_kind", "value": "backfill", "limit": 1}]
    for coordinator in (instance["run_coordinator"], compose["run_coordinator"]):
        assert coordinator["class"] == "QueuedRunCoordinator"
        assert coordinator["config"]["max_concurrent_runs"] == 3
        assert coordinator["config"]["tag_concurrency_limits"] == backfills


@pytest.mark.parametrize(
    "name",
    ["srdp-dagster-user-deployments-srdp-etl", "api", "duckdb-ui", "marimo", "streamlit"],
)
def test_ducklake_readers_and_writers_share_one_data_volume(local: list[Manifest], name: str) -> None:
    spec = find(local, "Deployment", name)["spec"]["template"]["spec"]
    claims = [v["persistentVolumeClaim"]["claimName"] for v in spec.get("volumes", []) if "persistentVolumeClaim" in v]
    assert claims == ["ducklake-data"]
    container = spec["containers"][0]
    env = {e["name"]: e.get("value") for e in container.get("env", [])}
    mount_paths = [m["mountPath"] for m in container.get("volumeMounts", [])]
    assert env["DUCKLAKE_DATA_PATH"] in mount_paths


# Development credentials that used to live in values.yaml. They may only
# appear in the kind-only Secrets that templates/local-secrets.yaml renders.
DEV_CREDENTIALS = (
    "srdpTest123",
    "51a69a373f45c60d2ae08c48bb89d03e",
    "VQMve4Thh857uplEKmN5nlcgSedaGyYQySSDYyoMgfk4d1PS8k6zUDSdhOdo3IVW",
    "6wdizcEbBnztdVvVoFwbSzHBfWYdBJshIOP6VlsxrDe5c1zSUQMvgDa6PfnA24BT",
    "VcZnfAWYCgMfNjRLvM1byUaAUs2jSvSE",
)

LOCAL_SECRETS = {"srdp-postgres", "srdp-zitadel", "srdp-oauth2-proxy", "srdp-dagster-postgresql", "srdp-marquez"}


def test_values_yaml_holds_no_credentials() -> None:
    text = (CHART_DIR / "values.yaml").read_text()
    for credential in DEV_CREDENTIALS:
        assert credential not in text


def test_default_render_holds_no_credentials_and_no_local_secrets() -> None:
    manifests = render("values.yaml")
    text = yaml.safe_dump_all(manifests)
    for credential in DEV_CREDENTIALS:
        assert credential not in text
    secret_names = {m["metadata"]["name"] for m in manifests if m["kind"] == "Secret"}
    assert not secret_names & LOCAL_SECRETS


def test_local_render_holds_credentials_only_in_local_secrets(local: list[Manifest]) -> None:
    rest = [m for m in local if not (m["kind"] == "Secret" and m["metadata"]["name"] in LOCAL_SECRETS)]
    text = yaml.safe_dump_all(rest)
    for credential in DEV_CREDENTIALS:
        assert credential not in text
    assert {m["metadata"]["name"] for m in local if m["kind"] == "Secret"} >= LOCAL_SECRETS


def test_every_password_env_var_comes_from_a_secret(local: list[Manifest]) -> None:
    for name, spec in pod_specs(local):
        for container in containers(spec):
            for env in container.get("env", []):
                if "PASSWORD" in env["name"] or "SECRET" in env["name"]:
                    assert "secretKeyRef" in env.get("valueFrom", {}), f"{name}/{container['name']}: {env['name']}"


def test_setup_job_and_marquez_read_the_same_marquez_password(local: list[Manifest]) -> None:
    def ref(spec: Manifest, var: str) -> Manifest:
        env = {e["name"]: e for c in spec["containers"] for e in c.get("env", [])}
        return env[var]["valueFrom"]["secretKeyRef"]

    setup = find(local, "Job", "srdp-setup")["spec"]["template"]["spec"]
    marquez = find(local, "Deployment", "marquez")["spec"]["template"]["spec"]
    assert ref(setup, "SETUP_PASSWORDS__MARQUEZ") == ref(marquez, "MARQUEZ_DB_PASSWORD")


def srdp_images(manifests: list[Manifest], registry: str) -> list[str]:
    """Return every image built by this repo, on the given registry or the default one."""
    return [
        c["image"]
        for _, spec in pod_specs(manifests)
        for c in containers(spec)
        if c["image"].startswith(registry) or "srdp-registry" in c["image"]
    ]


def just_evaluate(variable: str, *overrides: str) -> list[str]:
    """Return a Justfile `--set`/`--set-string` args variable as `--set` values, with `--set` overrides."""
    just = shutil.which("just")
    if just is None:
        pytest.skip("needs just")
    # Fixed argv, no shell: every argument comes from this test module.
    result = subprocess.run(  # noqa: S603
        [just, *overrides, "--evaluate", variable], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    args = shlex.split(result.stdout)
    assert set(args[::2]) <= {"--set", "--set-string"}, args
    return args[1::2]


def test_the_justfile_registry_moves_every_srdp_image() -> None:
    registry = "registry.example.com/acme"
    manifests = render(
        "values.yaml",
        "values-local.yaml",
        # The Dagster code location image is a subchart value the chart cannot
        # template, so registry_args also sets its repository.
        set_values=(
            *just_evaluate("registry_args", "--set", "registry", registry),
            "global.imagePullSecrets[0].name=registry-key",
        ),
    )
    images = srdp_images(manifests, registry)
    names = {image.rsplit("/", 1)[1].split(":")[0] for image in images}
    assert names >= {"marimo", "srdp-etl", "srdp-api", "streamlit"}
    assert all(image.startswith(f"{registry}/") for image in images), images
    # The platform images SRDP publishes stay on their own registry.
    platform = {i for i in manifest_images(manifests) if i.startswith(f"{PLATFORM_REGISTRY}/")}
    assert {i.rsplit("/", 1)[1].split(":")[0] for i in platform} >= {
        "srdp-setup",
        "duckdb-ui",
        "hub",
        "dagster-webserver",
    }
    for name in ("marimo", "api", "streamlit", "srdp-setup"):
        spec = next(s for n, s in pod_specs(manifests) if n == name)
        assert spec["imagePullSecrets"] == [{"name": "registry-key"}]


@pytest.mark.parametrize("values_file", ["values.yaml", "values-local.yaml", "values-prod.example.yaml"])
def test_chart_registry_defaults_match_srdp_toml(values_file: str) -> None:
    """Rendering without the Justfile must not land on a registry other than srdp.toml's."""
    registry = tomllib.loads((REPO_ROOT / "srdp.toml").read_text())["deploy"]["registry"]
    # The Justfile puts it inside single-quoted shell args and Helm --set values,
    # so a quote, comma or space would break or split them.
    assert re.fullmatch(r"[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)*", registry), registry
    values = yaml.safe_load((CHART_DIR / values_file).read_text())
    if "srdpRegistry" in values.get("global", {}):
        assert values["global"]["srdpRegistry"] == registry
    deployments = values["dagster"]["dagster-user-deployments"]["deployments"]
    assert [d["image"]["repository"] for d in deployments] == [f"{registry}/srdp-etl"]


LOCAL_SECRET_READERS = ["marquez", "api", "duckdb-ui", "marimo", "streamlit"]
SUBCHART_SECRET_READERS = [
    "srdp-oauth2-proxy",
    "srdp-dagster-webserver",
    "srdp-dagster-daemon",
    "srdp-dagster-user-deployments-srdp-etl",
]


def pod_annotations(manifests: list[Manifest], name: str) -> dict[str, str]:
    """Return the pod template annotations of the Deployment with this name."""
    return find(manifests, "Deployment", name)["spec"]["template"]["metadata"].get("annotations") or {}


@pytest.mark.parametrize("name", LOCAL_SECRET_READERS)
def test_local_secret_change_rolls_chart_pods(local: list[Manifest], name: str) -> None:
    """A secretKeyRef does not change the pod spec, so a checksum annotation rolls the pod instead."""
    changed = render("values.yaml", "values-local.yaml", set_values=("localSecrets.marquez.dbPassword=changed",))
    before = pod_annotations(local, name)["checksum/local-secrets"]
    assert pod_annotations(changed, name)["checksum/local-secrets"] != before


def test_default_render_has_no_local_secrets_checksum() -> None:
    manifests = render("values.yaml")
    for name in LOCAL_SECRET_READERS:
        assert "checksum/local-secrets" not in pod_annotations(manifests, name), name


@pytest.mark.parametrize("name", SUBCHART_SECRET_READERS)
def test_local_deploy_rolls_subchart_pods_on_local_secret_change(name: str) -> None:
    """Subcharts cannot hash the parent's Secrets, so local-deploy passes the checksum in."""
    manifests = render(
        "values.yaml",
        "values-local.yaml",
        set_values=tuple(just_evaluate("local_secrets_args", "--set", "local_secrets_sum", "abc")),
    )
    assert pod_annotations(manifests, name)["checksum/local-secrets"] == "abc"


def test_setup_job_outlives_its_success(local: list[Manifest]) -> None:
    """Its logs are the evidence the troubleshooting docs point to, so only the next hook run replaces it."""
    job = find(local, "Job", "srdp-setup")
    assert job["metadata"]["annotations"]["helm.sh/hook-delete-policy"] == "before-hook-creation"


@pytest.mark.parametrize(
    ("variable", "expected"),
    [
        ("prod_traefik_only_args", {"srdp-traefik", "hub"}),
        ("prod_auth_only_args", {"srdp-traefik", "hub", "srdp-zitadel", "srdp-zitadel-login", "srdp-oauth2-proxy"}),
    ],
)
def test_staged_prod_recipes_deploy_only_what_their_name_says(variable: str, expected: set[str]) -> None:
    manifests = render("values-prod.example.yaml", set_values=tuple(just_evaluate(variable)))
    assert {m["metadata"]["name"] for m in manifests if m["kind"] == "Deployment"} == expected


def test_writers_and_readers_mount_ducklake_data_at_the_same_path(local: list[Manifest]) -> None:
    names = ["srdp-dagster-user-deployments-srdp-etl", "api", "duckdb-ui", "marimo", "streamlit"]
    paths = set()
    for name in names:
        container = find(local, "Deployment", name)["spec"]["template"]["spec"]["containers"][0]
        paths |= {e["value"] for e in container["env"] if e["name"] == "DUCKLAKE_DATA_PATH"}
    assert len(paths) == 1, paths


# A digest, or a full major.minor.patch tag. Never `latest`.
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
EXACT_TAG = re.compile(r"v?\d+\.\d+\.\d+")
# Images that cannot be pinned from this repo, with the reason.
UNPINNABLE = {
    # Hardcoded in the zitadel subchart's login and setup templates.
    "wait4x/wait4x:3.6",
}
PLATFORM_REGISTRY = "ghcr.io/srdp-hub"
DOCKER_HUB_PREFIXES = ("registry-1.docker.io/", "docker.io/", "library/")


def split_image(image: str) -> tuple[str, str, str]:
    """Split an image reference into (name, tag, digest), with Docker Hub prefixes removed."""
    reference, _, digest = image.partition("@")
    name, tag = reference, ""
    if ":" in reference.rsplit("/", 1)[-1]:
        name, tag = reference.rsplit(":", 1)
    for prefix in DOCKER_HUB_PREFIXES:
        name = name.removeprefix(prefix)
    return name, tag, digest


def is_pinned(image: str) -> bool:
    """Return whether the image has a digest or an exact version tag, and isn't `latest`."""
    _, tag, _ = split_image(image)
    if tag == "latest":
        return False
    return bool(DIGEST.search(image) or EXACT_TAG.fullmatch(tag))


def manifest_images(node: object) -> list[str]:
    """Return the image of every container and init container anywhere in the manifests."""
    if isinstance(node, list):
        return [image for item in node for image in manifest_images(item)]
    if not isinstance(node, dict):
        return []
    images = [c["image"] for key in ("containers", "initContainers") for c in node.get(key) or []]
    return images + [image for value in node.values() for image in manifest_images(value)]


def compose_images() -> list[str]:
    """Return the third-party image of every Compose service, with `${VAR:-default}` resolved to its default."""
    compose = yaml.safe_load((REPO_ROOT / "deploy" / "docker" / "docker-compose.yml").read_text())
    # Services with a build are our own images, tagged by SRDP_VERSION instead of pinned.
    images = [s["image"] for s in compose["services"].values() if "image" in s and "build" not in s]
    return [re.sub(r"\$\{\w+:-(.*)\}", r"\1", image) for image in images]


FROM_LINE = re.compile(r"FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)


def dockerfile_bases() -> list[tuple[str, str]]:
    """Return (Dockerfile, base image) for every FROM line in the repo's Dockerfiles."""
    dockerfiles = [
        path
        for pattern in ("**/Dockerfile", "**/*.Dockerfile")
        for path in REPO_ROOT.glob(pattern)
        if not {".venv", "node_modules", ".git", ".claude"} & set(path.relative_to(REPO_ROOT).parts)
    ]
    bases = []
    for path in dockerfiles:
        stages: set[str] = set()
        for line in path.read_text().splitlines():
            match = FROM_LINE.match(line)
            if match is None:
                continue
            if match[1] not in stages and match[1] != "scratch":
                bases.append((str(path.relative_to(REPO_ROOT)), match[1]))
            if match[2]:
                stages.add(match[2])
    return bases


RENDERS = {
    "default": (("values.yaml",), ()),
    "local": (("values.yaml", "values-local.yaml"), ()),
    "prod": (("values-prod.example.yaml",), ()),
    "prod-with-metrics": (("values-prod.example.yaml",), ("zitadel-db.metrics.enabled=true",)),
}


@pytest.mark.parametrize("case", RENDERS)
def test_every_chart_image_is_pinned(case: str) -> None:
    values_files, set_values = RENDERS[case]
    images = set(manifest_images(render(*values_files, set_values=set_values)))
    # Our own images are tagged with the release (or dev locally), which the next tests check.
    own = {i for i in images if "srdp-registry/" in i or i.startswith(f"{PLATFORM_REGISTRY}/")}
    unpinned = {i for i in images if not is_pinned(i)} - own - UNPINNABLE
    assert not unpinned, unpinned


def test_every_compose_image_is_pinned_by_digest() -> None:
    images = compose_images()
    assert images
    assert [i for i in images if not (is_pinned(i) and DIGEST.search(i))] == []


def test_every_dockerfile_base_is_pinned_by_digest() -> None:
    bases = dockerfile_bases()
    assert bases
    assert [(path, image) for path, image in bases if not DIGEST.search(image)] == []


@pytest.mark.parametrize("case", ["local", "prod"])
def test_compose_and_chart_run_the_same_versions(case: str) -> None:
    """Every image Compose pulls runs in the chart too, at the same tag, and the same digest where both pin one."""
    values_files, set_values = RENDERS[case]
    chart = [split_image(i) for i in manifest_images(render(*values_files, set_values=set_values))]
    for name, tag, digest in map(split_image, compose_images()):
        versions = {(t, d) for n, t, d in chart if n == name}
        assert versions, f"{name} is not in the chart"
        for chart_tag, chart_digest in versions:
            assert chart_tag == tag, f"{name}: chart {chart_tag}, Compose {tag}"
            assert not chart_digest or chart_digest == digest, f"{name}: chart and Compose digests differ"


def chart_app_version() -> str:
    """Return the chart's appVersion, which is the SRDP release."""
    return yaml.safe_load((CHART_DIR / "Chart.yaml").read_text())["appVersion"]


@pytest.mark.parametrize("values_file", ["values.yaml", "values-prod.example.yaml"])
def test_release_tags_follow_the_chart_app_version(values_file: str) -> None:
    """Subchart image tags cannot be templated, so the release script keeps them in step with appVersion."""
    marked = re.findall(r'tag: "([^"]+)" # release-tag', (CHART_DIR / values_file).read_text())
    assert marked
    assert set(marked) == {chart_app_version()}


def test_chart_version_is_the_release_version() -> None:
    chart = yaml.safe_load((CHART_DIR / "Chart.yaml").read_text())
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]
    assert chart["version"] == chart["appVersion"] == project["version"]


@pytest.mark.parametrize("case", ["default", "local", "prod"])
def test_dagster_runs_srdp_images_only(case: str) -> None:
    """The webserver and daemon run our dagster-webserver image, so Dagster and Python match uv.lock."""
    values_files, set_values = RENDERS[case]
    images = manifest_images(render(*values_files, set_values=set_values))
    assert [i for i in images if "dagster/" in i and PLATFORM_REGISTRY not in i] == []
    assert any(i.startswith(f"{PLATFORM_REGISTRY}/dagster-webserver:") for i in images)


def test_compose_and_chart_run_the_same_postgres_major() -> None:
    """The chart's Postgres is a Bitnami image pinned by digest, so its major comes from the subchart's appVersion."""
    compose = yaml.safe_load((REPO_ROOT / "deploy" / "docker" / "docker-compose.yml").read_text())
    compose_major = split_image(compose["services"]["postgres"]["image"])[1].split("-")[0]
    with tarfile.open(next((CHART_DIR / "charts").glob("postgresql-*.tgz"))) as archive:
        subchart = yaml.safe_load(archive.extractfile("postgresql/Chart.yaml").read())
    assert subchart["appVersion"].split(".")[0] == compose_major
