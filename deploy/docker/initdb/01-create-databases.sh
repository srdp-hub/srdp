#!/bin/sh
# Create service databases and roles for the SRDP platform.
# This script runs once when the PostgreSQL data directory is first initialised.
# A .sh (not .sql) file specifically so the postgres image's own entrypoint
# passes it through the shell first, letting the heredoc below expand the
# ZITADEL_DATABASE_POSTGRES_USER_PASSWORD/DAGSTER_PG_PASSWORD env vars (set on
# this container in docker-compose.yml, sourced from .env) rather than needing
# literal passwords committed here.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Zitadel
    CREATE USER zitadel WITH PASSWORD '${ZITADEL_DATABASE_POSTGRES_USER_PASSWORD}';
    CREATE DATABASE zitadel OWNER zitadel;

    -- Dagster
    CREATE USER dagster WITH PASSWORD '${DAGSTER_PG_PASSWORD}';
    CREATE DATABASE dagster OWNER dagster;

    -- Marquez (OpenLineage backend). Credentials match marquez.dev.yml's hardcoded
    -- defaults (db.user/db.password = "marquez") -- no env-var override for those two
    -- in the image's default config, only POSTGRES_HOST/POSTGRES_PORT are templated.
    -- Not a candidate for the same externalization as the other two passwords here.
    CREATE USER marquez WITH PASSWORD 'marquez';
    CREATE DATABASE marquez OWNER marquez;
EOSQL
