"""Kubernetes run config profiles for Dagster job tags.

Import these in any project's definitions.py and apply them as job tags:

    srdp_job = define_asset_job(
        "srdp_job",
        tags={"dagster-k8s/config": BASE_RUN_K8S_CONFIG, "dagster/priority": "0"},
    )
"""

# A full srdp_etl_job run (multiprocess executor, dbt, DuckDB) peaks at about
# 1Gi in kind, so 512Mi got it OOMKilled. The limit leaves headroom above that,
# and the fast lane keeps the same limit, so a full job there doesn't OOM either.
BASE_RUN_K8S_CONFIG = {
    "container_config": {
        "resources": {
            "requests": {"cpu": "250m", "memory": "512Mi"},
            "limits": {"cpu": "1", "memory": "1536Mi"},
        }
    },
    "job_metadata": {
        "labels": {
            "workload": "etl",
            "team": "data-platform",
        }
    },
}

FAST_LANE_K8S_CONFIG = {
    **BASE_RUN_K8S_CONFIG,
    "container_config": {
        "resources": {
            "requests": {"cpu": "500m", "memory": "512Mi"},
            "limits": {"cpu": "1", "memory": "1536Mi"},
        }
    },
    "job_spec_config": {
        "ttl_seconds_after_finished": 600,
        "active_deadline_seconds": 900,
    },
    "job_metadata": {
        "labels": {
            "workload": "etl-fast-lane",
            "team": "data-platform",
        }
    },
}

BACKFILL_K8S_CONFIG = {
    **BASE_RUN_K8S_CONFIG,
    "container_config": {
        "resources": {
            "requests": {"cpu": "750m", "memory": "1Gi"},
            "limits": {"cpu": "2", "memory": "2Gi"},
        }
    },
    "job_spec_config": {
        "ttl_seconds_after_finished": 3600,
        "active_deadline_seconds": 7200,
    },
    "job_metadata": {
        "labels": {
            "workload": "etl-backfill",
            "team": "data-platform",
        }
    },
}
