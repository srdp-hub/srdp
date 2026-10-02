"""Render the Helm chart with `helm template` and check its contract with the Compose stack."""

import re
import shlex
import shutil
import subprocess
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
    assert container["image"].endswith("/streamlit:v1.0")
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
    "1" * 64,
    "2" * 64,
    "3" * 64,
    "srdpLocalGarageAdmin",
)

LOCAL_SECRETS = {
    "srdp-postgres",
    "srdp-zitadel",
    "srdp-oauth2-proxy",
    "srdp-dagster-postgresql",
    "srdp-marquez",
    "srdp-ducklake-s3-writer",
    "srdp-ducklake-s3-reader",
    "srdp-garage",
}


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
    assert names >= {"marimo", "srdp-etl", "srdp-api", "duckdb-ui", "hub", "streamlit", "srdp-setup"}
    assert all(image.startswith(f"{registry}/") for image in images), images
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


# DuckLake on S3 (ticket 02, PR 2b). values-local-s3.yaml switches kind to the
# bundled Garage server. A SQL console runs with the full authority of its S3
# key, so the four apps must only ever see the read-only key.
DUCKLAKE_WRITER = "srdp-dagster-user-deployments-srdp-etl"
DUCKLAKE_READERS = ["api", "duckdb-ui", "marimo", "streamlit"]
READER_KEY = "srdp-ducklake-s3-reader"
WRITER_KEY = "srdp-ducklake-s3-writer"


@pytest.fixture(scope="module")
def local_s3() -> list[Manifest]:
    """Render the chart the way `just local-deploy -f srdp-chart/values-local-s3.yaml` does."""
    return render("values.yaml", "values-local.yaml", "values-local-s3.yaml")


def s3_key_refs(env: list[Manifest]) -> list[Manifest]:
    """Return the secretKeyRef of every DUCKLAKE_S3_* variable in an env list."""
    return [e["valueFrom"]["secretKeyRef"] for e in env if e["name"].startswith("DUCKLAKE_S3_") and "valueFrom" in e]


def run_pod_env(manifests: list[Manifest]) -> list[Manifest]:
    """Return the env the Dagster code location hands to every run pod it launches."""
    container = find(manifests, "Deployment", DUCKLAKE_WRITER)["spec"]["template"]["spec"]["containers"][0]
    context = next(e["value"] for e in container["env"] if e["name"] == "DAGSTER_CLI_API_GRPC_CONTAINER_CONTEXT")
    return yaml.safe_load(context)["k8s"]["env"]


@pytest.mark.parametrize("name", DUCKLAKE_READERS)
def test_s3_apps_get_only_the_read_only_key(local_s3: list[Manifest], name: str) -> None:
    spec = find(local_s3, "Deployment", name)["spec"]["template"]["spec"]
    refs = [ref for c in containers(spec) for ref in s3_key_refs(c.get("env", []))]
    assert {ref["name"] for ref in refs} == {READER_KEY}
    assert WRITER_KEY not in yaml.safe_dump(spec)


def test_s3_dagster_and_its_run_pods_get_only_the_writer_key(local_s3: list[Manifest]) -> None:
    spec = find(local_s3, "Deployment", DUCKLAKE_WRITER)["spec"]["template"]["spec"]
    refs = s3_key_refs(spec["containers"][0]["env"])
    assert {ref["name"] for ref in refs} == {WRITER_KEY}
    assert {ref["name"] for ref in s3_key_refs(run_pod_env(local_s3))} == {WRITER_KEY}
    assert READER_KEY not in yaml.safe_dump(spec)


@pytest.mark.parametrize("name", [DUCKLAKE_WRITER, *DUCKLAKE_READERS])
def test_every_ducklake_consumer_reads_one_storage_config(local_s3: list[Manifest], name: str) -> None:
    container = find(local_s3, "Deployment", name)["spec"]["template"]["spec"]["containers"][0]
    assert {"configMapRef": {"name": "srdp-ducklake-storage"}} in container["envFrom"]
    storage = find(local_s3, "ConfigMap", "srdp-ducklake-storage")["data"]
    assert storage["DUCKLAKE_STORAGE_BACKEND"] == "s3"
    assert storage["DUCKLAKE_S3_ENDPOINT"] == "garage:3900"


def test_local_storage_stays_the_default_and_needs_no_s3_key(local: list[Manifest]) -> None:
    assert find(local, "ConfigMap", "srdp-ducklake-storage")["data"]["DUCKLAKE_STORAGE_BACKEND"] == "local"
    for name in [DUCKLAKE_WRITER, *DUCKLAKE_READERS]:
        container = find(local, "Deployment", name)["spec"]["template"]["spec"]["containers"][0]
        assert all(ref["optional"] is True for ref in s3_key_refs(container["env"])), name
    assert all(ref["optional"] is True for ref in s3_key_refs(run_pod_env(local)))
    assert not [m for m in local if m["kind"] in {"Deployment", "StatefulSet"} and m["metadata"]["name"] == "garage"]


def test_s3_setup_job_gives_garage_exactly_the_keys_the_consumers_get(local_s3: list[Manifest]) -> None:
    assert not [m for m in local_s3 if m["kind"] == "Job" and m["metadata"]["name"] == "garage-setup"]
    env = find(local_s3, "Job", "srdp-setup")["spec"]["template"]["spec"]["containers"][0]["env"]
    refs = {e["name"]: e["valueFrom"]["secretKeyRef"] for e in env if "valueFrom" in e}
    assert refs["GARAGE_WRITER_KEY_ID"] == {"name": WRITER_KEY, "key": "DUCKLAKE_S3_KEY_ID"}
    assert refs["GARAGE_WRITER_SECRET"] == {"name": WRITER_KEY, "key": "DUCKLAKE_S3_SECRET"}
    assert refs["GARAGE_READER_KEY_ID"] == {"name": READER_KEY, "key": "DUCKLAKE_S3_KEY_ID"}
    assert refs["GARAGE_READER_SECRET"] == {"name": READER_KEY, "key": "DUCKLAKE_S3_SECRET"}
    storage = find(local_s3, "ConfigMap", "srdp-ducklake-storage")["data"]
    assert next(e["value"] for e in env if e["name"] == "GARAGE_BUCKET") == storage["DUCKLAKE_S3_BUCKET"]


def test_setup_job_skips_the_garage_step_without_garage(local: list[Manifest]) -> None:
    env = find(local, "Job", "srdp-setup")["spec"]["template"]["spec"]["containers"][0]["env"]
    assert not [e["name"] for e in env if e["name"].startswith("GARAGE_")]


def bucket_wait(spec: Manifest) -> Manifest:
    """Return the wait-for-ducklake-bucket init container of a pod spec."""
    [container] = [c for c in spec.get("initContainers", []) if c["name"] == "wait-for-ducklake-bucket"]
    return container


@pytest.mark.parametrize(("name", "key"), [(DUCKLAKE_WRITER, WRITER_KEY)] + [(n, READER_KEY) for n in DUCKLAKE_READERS])
def test_s3_every_consumer_waits_for_the_bucket_with_its_own_key(local_s3: list[Manifest], name: str, key: str) -> None:
    wait = bucket_wait(find(local_s3, "Deployment", name)["spec"]["template"]["spec"])
    assert {"configMapRef": {"name": "srdp-ducklake-storage"}} in wait["envFrom"]
    assert {ref["name"] for ref in s3_key_refs(wait["env"])} == {key}


def test_s3_dagster_run_pods_wait_for_the_bucket_too(local_s3: list[Manifest]) -> None:
    container = find(local_s3, "Deployment", DUCKLAKE_WRITER)["spec"]["template"]["spec"]["containers"][0]
    context = next(e["value"] for e in container["env"] if e["name"] == "DAGSTER_CLI_API_GRPC_CONTAINER_CONTEXT")
    init = yaml.safe_load(context)["k8s"]["run_k8s_config"]["pod_spec_config"]["init_containers"]
    [wait] = [c for c in init if c["name"] == "wait-for-ducklake-bucket"]
    assert {ref["name"] for ref in s3_key_refs(wait["env"])} == {WRITER_KEY}


def test_s3_every_wait_container_runs_hardened(local_s3: list[Manifest]) -> None:
    # Only the chart's own waits, subcharts such as Zitadel bring their own.
    own = re.compile(r"wait-for-[a-z0-9-]+-(db|bucket)")
    waits = [
        (name, c)
        for name, spec in pod_specs(local_s3)
        for c in spec.get("initContainers", [])
        if own.fullmatch(c["name"])
    ]
    assert {c["name"] for _, c in waits} >= {"wait-for-ducklake-db", "wait-for-ducklake-bucket"}
    for name, c in waits:
        context = c.get("securityContext", {})
        assert context.get("runAsNonRoot") is True, f"{name}/{c['name']}"
        assert context.get("readOnlyRootFilesystem") is True, f"{name}/{c['name']}"
        assert context.get("allowPrivilegeEscalation") is False, f"{name}/{c['name']}"
        assert context.get("capabilities") == {"drop": ["ALL"]}, f"{name}/{c['name']}"
