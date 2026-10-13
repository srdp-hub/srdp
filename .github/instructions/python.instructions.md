---
applyTo: "**/*.py"
---

## Python file rules

### Imports

- Do not add `from __future__ import annotations`. Python 3.12 supports native type hints.
- Order imports as stdlib, third-party, first-party (`srdp.*`), then local.
- Use absolute imports only.
- Put imports that only type checking needs under `TYPE_CHECKING`.

### Docstrings

Give every public symbol a docstring.

```python
# Correct
def table_ref(self, asset_key_path: list[str]) -> str:
    """Derive a fully-qualified DuckLake table reference from an asset key path.

    Args:
        asset_key_path: The asset key path segments, e.g. ``["raw", "orders"]``.

    Returns:
        Fully-qualified reference like ``ducklake.raw.orders``.
    """

# Wrong: no docstring
def table_ref(self, asset_key_path: list[str]) -> str:
    ...
```

### Type annotations

Annotate every signature fully. Use native syntax only.

```python
# Correct
def create_connection(
    settings: DuckLakeSettings,
    backend: StorageBackend | None = None,
) -> duckdb.DuckDBPyConnection:

# Wrong: no annotations
def create_connection(settings, backend=None):

# Wrong: legacy typing module
def create_connection(
    settings: DuckLakeSettings,
    backend: Optional[StorageBackend] = None,
) -> duckdb.DuckDBPyConnection:
```

### Functions

Give each function one responsibility.

```python
# Correct
def _ensure_schema(conn: duckdb.DuckDBPyConnection, schema: str) -> None:
    """Create the DuckLake schema if it does not already exist."""
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS ducklake.{schema}")  # noqa: S608

# Wrong: too many responsibilities
def setup_and_write(settings, df, schema, table):
    conn = duckdb.connect()
    conn.execute("INSTALL ducklake")
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    conn.execute(f"CREATE TABLE {schema}.{table} AS SELECT * FROM df")
```

### Settings

Read configuration through pydantic-settings. Do not read `os.environ`.

```python
# Correct
class DuckLakeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DUCKLAKE_", env_file=".env")
    pg_host: str = Field(default="localhost")
    pg_password: str  # required, no default

# Wrong
pg_host = os.environ.get("DUCKLAKE_PG_HOST", "localhost")
```

### Logging

Use `logging`. Do not use `print()`.

```python
# Correct
import logging
logger = logging.getLogger(__name__)
logger.info("Processing %d rows.", count)

# Wrong
print(f"Processing {count} rows")
```

### SQL safety

Put only validated identifiers in SQL strings. Never put user input in SQL.

```python
# Correct: identifiers come from validated asset keys
conn.execute(f"SELECT * FROM {ref}")  # noqa: S608

# Wrong: user input in SQL
conn.execute(f"SELECT * FROM {user_input}")
```

### Classes

- Use classes to group related functions. Do not use them to hold mutable state.
- Return values. Do not change arguments.
- Put only imports and a module docstring in `__init__.py`.

### Code organization

- Put reusable platform code in `src/srdp/`. Do not put it in a project.
- Keep each submodule self-contained.
- Put assets and definitions in `src/srdp/assets/` or `projects/<name>/src/etl/assets/`.

### Testing

- Use `pytest`. Write tests as plain functions.
- Use fixtures for shared setup.
- Test only our own code.
- Mirror the source structure under `tests/`.
