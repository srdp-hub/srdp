"""Catalog step of setup: read-only access for the apps, and the first attach as the writer.

The step grants the reader its rights, hands an older catalog to the owner role, and
creates the catalog tables once, because a reader cannot create them.
"""

import logging

import psycopg2.extensions
from psycopg2 import sql

from srdp.io.ducklake import DuckLakeSettings, initialize_catalog
from srdp.setup.bootstrap import DatabaseTarget, SetupSettings, _connect_with_retry

logger = logging.getLogger(__name__)

_SCHEMA = sql.Identifier("public")


def _hand_over(cur: psycopg2.extensions.cursor, owner: str) -> None:
    """Make ``owner`` own every table, sequence and view in the schema, as the owner role must change them."""
    queries = (
        ("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tableowner <> %s", "TABLE"),
        ("SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' AND sequenceowner <> %s", "SEQUENCE"),
        ("SELECT viewname FROM pg_views WHERE schemaname = 'public' AND viewowner <> %s", "VIEW"),
    )
    for query, kind in queries:
        cur.execute(query, (owner,))
        for (name,) in cur.fetchall():
            cur.execute(
                sql.SQL("ALTER {} {}.{} OWNER TO {}").format(
                    sql.SQL(kind), _SCHEMA, sql.Identifier(name), sql.Identifier(owner)
                )
            )
            logger.info("Handed %s '%s' to '%s'.", kind.lower(), name, owner)


def reconcile_catalog_access(cur: psycopg2.extensions.cursor, target: DatabaseTarget) -> None:
    """Give the owner role the schema and the reader role SELECT, and nothing more.

    The statements are idempotent, so every deploy repairs the rights. The default
    privileges cover tables the owner creates later, such as DuckLake's inlined-data tables.

    Args:
        cur: A cursor connected to the catalog database as the Postgres superuser.
        target: The catalog database. It needs both ``role`` and ``reader_role``.
    """
    if target.role is None or target.reader_role is None:
        msg = f"Database '{target.name}' needs a role and a reader_role."
        raise ValueError(msg)
    owner = sql.Identifier(target.role)
    reader = sql.Identifier(target.reader_role)

    # Before Postgres 15 every role can create objects in the public schema.
    cur.execute(sql.SQL("REVOKE CREATE ON SCHEMA {} FROM PUBLIC").format(_SCHEMA))
    cur.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA {} TO {}").format(_SCHEMA, owner))
    _hand_over(cur, target.role)

    cur.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(_SCHEMA, reader))
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(_SCHEMA, reader))
    cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(_SCHEMA, reader))
    cur.execute(
        sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(
            owner, _SCHEMA, reader
        )
    )
    logger.info("Role '%s' reads '%s', role '%s' owns it.", target.reader_role, target.name, target.role)


def create_catalog(settings: SetupSettings, target: DatabaseTarget) -> None:
    """Create the catalog tables as the owner role, with the storage settings from ``DUCKLAKE_*``.

    Args:
        settings: Setup settings, holding the owner password and the Postgres address.
        target: The catalog database.
    """
    password = settings.passwords.get(target.role or "")
    if target.role is None or password is None or not password.get_secret_value():
        logger.warning("No password for the owner of '%s', so setup does not create its catalog.", target.name)
        return
    ducklake = DuckLakeSettings(
        pg_host=settings.pg_host,
        pg_port=settings.pg_port,
        pg_user=target.role,
        pg_password=password.get_secret_value(),
        pg_db=target.name,
    )
    initialize_catalog(ducklake)


def setup_catalogs(settings: SetupSettings) -> None:
    """Reconcile the access and create the tables of every enabled target that has a reader role.

    Settings with ``manage_databases`` off skip the access step, because the operator owns the
    roles and grants then. The catalog tables are still created when the owner password is set.

    Args:
        settings: Validated setup settings.
    """
    for target in settings.databases:
        if not target.enabled or target.reader_role is None:
            continue
        if settings.manage_databases:
            conn = _connect_with_retry(settings, dbname=target.name)
            try:
                # One transaction, so a running app never sees the reader without its SELECT.
                with conn, conn.cursor() as cur:
                    reconcile_catalog_access(cur, target)
            finally:
                conn.close()
        else:
            logger.info("manage_databases is off, so setup leaves the grants on '%s' to the operator.", target.name)
        create_catalog(settings, target)
