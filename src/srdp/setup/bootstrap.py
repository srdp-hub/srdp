"""Idempotent Postgres database/role bootstrap, from the ``[setup]`` table of ``srdp.toml``.

Role passwords come from ``SETUP_PASSWORDS__<ROLE>``. Every run also resets them.
"""

import logging
import time
from pathlib import Path
from typing import Annotated

import psycopg2
import psycopg2.extensions
from psycopg2 import sql
from psycopg2.extensions import encrypt_password
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from srdp.setup.garage import GarageTarget

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("/etc/srdp/srdp.toml")
# Per connection attempt, so an unreachable host fails fast and the retry loop,
# not a hanging socket, decides how long setup waits.
CONNECT_TIMEOUT_SECONDS = 5
CONNECT_ATTEMPTS = 40
CONNECT_RETRY_DELAY_SECONDS = 3.0
# The longest setup waits for Postgres. The chart's setup.activeDeadlineSeconds
# must cover it plus the Garage step's wait, a test checks.
DATABASE_WAIT_SECONDS = CONNECT_ATTEMPTS * (CONNECT_TIMEOUT_SECONDS + CONNECT_RETRY_DELAY_SECONDS)


# Lowercase, and within Postgres's 63-byte identifier limit, since a longer
# name is truncated on creation and never matched again.
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z_][a-z0-9_]{0,62}$")]


class DatabaseTarget(BaseModel):
    """One database (and optionally its own role) the setup service ensures exists."""

    model_config = ConfigDict(extra="forbid")

    name: Identifier
    # Lowercase, SETUP_PASSWORDS__<ROLE> keys are lowercased.
    # None reuses the superuser as owner.
    role: Identifier | None = None
    # A second role that can only read the database. Setup grants it SELECT on every table the
    # owner creates. Only the DuckLake catalog uses it, for the apps that run user SQL.
    reader_role: Identifier | None = None
    # False leaves the target out of setup, checks included.
    enabled: bool = True

    @property
    def roles(self) -> tuple[str, ...]:
        """The roles of this target, the owner first."""
        return tuple(role for role in (self.role, self.reader_role) if role is not None)

    @model_validator(mode="after")
    def _check_reader(self) -> "DatabaseTarget":
        if self.reader_role is not None and self.role is None:
            msg = f"Database '{self.name}' has a `reader_role`, so it needs a `role` that owns it."
            raise ValueError(msg)
        if self.reader_role is not None and self.reader_role == self.role:
            msg = f"Database '{self.name}' uses '{self.role}' as owner and as reader, give the reader its own role."
            raise ValueError(msg)
        return self


class SetupSettings(BaseSettings):
    """Superuser connection, database targets, and per-role passwords."""

    model_config = SettingsConfigDict(
        env_prefix="SETUP_",
        env_nested_delimiter="__",
        # Split only once, so a role named a__b still maps to passwords["a__b"].
        env_nested_max_split=1,
        toml_file=CONFIG_PATH,
        toml_table_header=("setup",),
        extra="forbid",
        # The input holds role passwords and Garage's keys.
        hide_input_in_errors=True,
    )

    pg_host: str = Field(default="postgres")
    pg_port: int = Field(default=5432)
    pg_user: str = Field(default="postgres")
    pg_password: SecretStr = Field(validation_alias="POSTGRES_PASSWORD")
    # The DuckLake data volume, when this deployment mounts it into setup (Compose does).
    data_dir: Path | None = Field(default=None)
    databases: list[DatabaseTarget]
    # False for a managed Postgres. Setup then only checks the databases.
    manage_databases: bool = True
    # Keyed by role name, e.g. SETUP_PASSWORDS__MARQUEZ -> passwords["marquez"].
    passwords: dict[str, SecretStr] = Field(default_factory=dict)
    # The optional Garage step, [setup.garage] plus SETUP_GARAGE__* secrets.
    garage: GarageTarget = Field(default_factory=GarageTarget)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003 -- fixed hook signature
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003 -- fixed hook signature
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Read init kwargs, then env vars, then the TOML config file."""
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls))

    @model_validator(mode="after")
    def _check_targets(self) -> "SetupSettings":
        names = [target.name for target in self.databases]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            msg = f"Duplicate database names in setup config: {', '.join(duplicates)}."
            raise ValueError(msg)

        roles = [role for target in self.databases for role in target.roles]
        if self.pg_user in roles:
            msg = f"Role '{self.pg_user}' is the superuser, leave `role` out to use it."
            raise ValueError(msg)
        shared = sorted({role for role in roles if roles.count(role) > 1})
        if shared:
            msg = f"Role(s) {', '.join(shared)} belong to more than one database, give each database its own roles."
            raise ValueError(msg)

        if not self.manage_databases:
            return self

        missing = sorted(
            role
            for target in self.databases
            if target.enabled
            for role in target.roles
            if not (role in self.passwords and self.passwords[role].get_secret_value())
        )
        if missing:
            env_vars = ", ".join(f"SETUP_PASSWORDS__{role.upper()}" for role in missing)
            msg = f"No password set for enabled role(s) {', '.join(missing)}. Set {env_vars}."
            raise ValueError(msg)
        return self


def _role_exists(cur: psycopg2.extensions.cursor, role: str) -> bool:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    return cur.fetchone() is not None


def _database_exists(cur: psycopg2.extensions.cursor, database: str) -> bool:
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
    return cur.fetchone() is not None


def _ensure_role(cur: psycopg2.extensions.cursor, role: str, settings: SetupSettings) -> None:
    """Create ``role`` with login, or reset its password when it exists."""
    # Hashed client-side, so a logged statement never holds the plain password.
    password = encrypt_password(settings.passwords[role].get_secret_value(), role, cur, "scram-sha-256")
    identifier = sql.Identifier(role)
    if _role_exists(cur, role):
        cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(identifier), (password,))
        logger.info("Role '%s' already exists, password reconciled.", role)
    else:
        cur.execute(sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD %s").format(identifier), (password,))
        logger.info("Created role '%s'.", role)


def _reconcile_owner(cur: psycopg2.extensions.cursor, database: str, owner: str) -> None:
    """Hand ``database`` to ``owner`` when someone else owns it, as on an install from before the role split."""
    cur.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (database,))
    row = cur.fetchone()
    if row is not None and row[0] != owner:
        cur.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(database), sql.Identifier(owner)))
        logger.info("Database '%s' now belongs to '%s' (was '%s').", database, owner, row[0])


def _restrict_connect(cur: psycopg2.extensions.cursor, target: DatabaseTarget) -> None:
    """Let only the target's own roles connect, because Postgres lets every role connect by default."""
    database = sql.Identifier(target.name)
    cur.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(database))
    for role in target.roles:
        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, sql.Identifier(role)))


def ensure_target(cur: psycopg2.extensions.cursor, target: DatabaseTarget, settings: SetupSettings) -> None:
    """Idempotently create one target's roles and database, and reconcile passwords, owner and connect rights.

    Args:
        cur: An autocommit cursor connected as the Postgres superuser.
        target: The database (and optional roles) to ensure exists.
        settings: Validated settings, holding the role passwords.
    """
    for role in target.roles:
        _ensure_role(cur, role, settings)

    owner = target.role or settings.pg_user
    if _database_exists(cur, target.name):
        logger.info("Database '%s' already exists.", target.name)
        _reconcile_owner(cur, target.name, owner)
    else:
        cur.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(target.name), sql.Identifier(owner)),
        )
        logger.info("Created database '%s' (owner '%s').", target.name, owner)
    _restrict_connect(cur, target)


def check_targets(cur: psycopg2.extensions.cursor, settings: SetupSettings) -> None:
    """Fail unless every enabled target database already exists.

    Used when ``manage_databases`` is off. It changes nothing.

    Args:
        cur: A cursor connected to Postgres.
        settings: Validated settings, holding the targets.

    Raises:
        RuntimeError: If one or more databases are missing.
    """
    missing = [
        target.name for target in settings.databases if target.enabled and not _database_exists(cur, target.name)
    ]
    if missing:
        msg = f"Database(s) {', '.join(missing)} do not exist, and manage_databases is off. Create them first."
        raise RuntimeError(msg)
    logger.info("manage_databases is off, all databases exist.")


def _connect_with_retry(
    settings: SetupSettings,
    max_attempts: int = CONNECT_ATTEMPTS,
    delay_seconds: float = CONNECT_RETRY_DELAY_SECONDS,
    dbname: str = "postgres",
) -> psycopg2.extensions.connection:
    """Connect to Postgres, retrying while it's still starting up.

    On Kubernetes this Job runs as a post-install/post-upgrade hook: the
    Postgres resource exists by then, but its pod may not be accepting
    connections yet. Retrying here (rather than relying solely on the Job's
    own `backoffLimit`/pod restarts) avoids CrashLoopBackOff noise and is
    more forgiving of slow-starting clusters (PVC provisioning, cold pulls).

    Args:
        settings: Superuser connection settings.
        max_attempts: How many times to try before giving up.
        delay_seconds: Delay between attempts.
        dbname: The database to connect to. Role and database changes run in ``postgres``.

    Returns:
        An open connection.
    """
    last_error: psycopg2.OperationalError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return psycopg2.connect(
                host=settings.pg_host,
                port=settings.pg_port,
                user=settings.pg_user,
                password=settings.pg_password.get_secret_value(),
                dbname=dbname,
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except psycopg2.OperationalError as exc:
            last_error = exc
            logger.warning("Postgres not ready yet (attempt %d/%d): %s", attempt, max_attempts, exc)
            if attempt < max_attempts:
                time.sleep(delay_seconds)
    assert last_error is not None  # noqa: S101 -- loop always sets it before exhausting attempts
    raise last_error


def bootstrap_databases(settings: SetupSettings | None = None) -> None:
    """Ensure every enabled target database (and its role) exists, idempotently.

    Settings are validated before connecting, so a missing password fails the
    run before anything is created.

    Args:
        settings: Setup settings. Loaded from the environment and the TOML
            config file if not provided.
    """
    if settings is None:
        settings = SetupSettings()  # ty: ignore[missing-argument]

    conn = _connect_with_retry(settings)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            if not settings.manage_databases:
                check_targets(cur, settings)
                return
            for target in settings.databases:
                if not target.enabled:
                    logger.info("Skipping '%s', disabled in setup config.", target.name)
                    continue
                ensure_target(cur, target, settings)
    finally:
        conn.close()
