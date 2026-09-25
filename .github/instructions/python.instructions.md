---
applyTo: "**/*.py"
---

## Python file rules

### Imports

- Do NOT add `from __future__ import annotations` — the repo targets Python 3.12+ where native type hints work without it.
- Group order: stdlib → third-party → first-party (`srdp.*`) → local.
- No relative imports anywhere. Always absolute.
- Use `TYPE_CHECKING` guards for imports only needed at type-check time.

### Every public symbol needs a docstring

```python
# ✅ correct
def table_ref(self, asset_key_path: list[str]) -> str:
    """Derive a fully-qualified DuckLake table reference from an asset key path.

    Args:
        asset_key_path: The asset key path segments, e.g. ``["raw", "orders"]``.

    Returns:
        Fully-qualified reference like ``ducklake.raw.orders``.
    """

# ❌ wrong — missing docstring
def table_ref(self, asset_key_path: list[str]) -> str:
    ...
```

### All signatures fully annotated — native syntax only

```python
# ✅ correct
def create_connection(
    settings: DuckLakeSettings,
    backend: StorageBackend | None = None,
) -> duckdb.DuckDBPyConnection:

# ❌ wrong — missing annotations
def create_connection(settings, backend=None):

# ❌ wrong — legacy typing module
def create_connection(
    settings: DuckLakeSettings,
    backend: Optional[StorageBackend] = None,
) -> duckdb.DuckDBPyConnection:
```

### Small, focused functions

```python
# ✅ correct — one responsibility per function
def _ensure_schema(conn: duckdb.DuckDBPyConnection, schema: str) -> None:
    """Create the DuckLake schema if it does not already exist."""
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS ducklake.{schema}")  # noqa: S608

# ❌ wrong — doing too many things at once
def setup_and_write(settings, df, schema, table):
    conn = duckdb.connect()
    conn.execute("INSTALL ducklake")
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    conn.execute(f"CREATE TABLE {schema}.{table} AS SELECT * FROM df")
```

### Settings via pydantic-settings

```python
# ✅ correct
class DuckLakeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DUCKLAKE_", env_file=".env")
    pg_host: str = Field(default="localhost")
    pg_password: str  # required — no default

# ❌ wrong — reading os.environ directly
pg_host = os.environ.get("DUCKLAKE_PG_HOST", "localhost")
```

### Logging

```python
# ✅ correct
import logging
logger = logging.getLogger(__name__)
logger.info("Processing %d rows.", count)

# ❌ wrong
print(f"Processing {count} rows")
```

### SQL safety

```python
# ✅ acceptable — identifiers come from validated asset keys, not user input
conn.execute(f"SELECT * FROM {ref}")  # noqa: S608

# ❌ never — user-controlled strings in SQL
conn.execute(f"SELECT * FROM {user_input}")
```

### Classes

- Use classes to group related functionality, not to hold mutable state.
- Prefer returning values over mutating arguments.
- Don't put logic in `__init__.py` — only imports and a module-level docstring.

### Code organization

- Reusable platform code belongs in `src/srdp/`, not in a project directory.
- Each submodule should be self-contained with clear boundaries.
- Assets and definitions go in `src/srdp/assets/` or `projects/<name>/src/etl/assets/`, not in core or utils.

### Testing

- Use `pytest` for all tests. Write tests as plain functions, not inside classes.
- Use fixtures for shared setup.
- Only test our own code, not functionality from external packages.
- Test files mirror the source structure under `tests/`.
