---
applyTo: "**/*.py"
---

## Python file rules

Read `pyproject.toml` first. It sets the rules for ruff, ty and pytest.
This file gives the rules that a linter cannot check.

### Principles

- Build the simplest solution that works.
- Add no feature, parameter or abstraction before a story needs it.
- Give each function, class and module one responsibility.
- Put repeated logic in one place.
- Keep I/O at the edges. Keep the logic in pure functions.
- Handle an error near its source.

### Imports

- Use absolute imports only.
- Never add `from __future__ import annotations`.
- Group imports as stdlib, third-party, `srdp.*` and local.
- Use a `TYPE_CHECKING` guard for an import that only type checks need.

### Typing

- Type every signature, also the return type.
- Use native syntax, such as `str | None`. Never use `Optional` or `List`.
- Do not use `Any`. Use a Pydantic model, a `TypedDict` or a `Protocol`.
- Use a maximum of two types plus `None` in a union.
- Parse external data into a typed model at the boundary.
- Use a maximum of four parameters. Put the rest in a model.

### Classes

- Use a module for functions that share nothing.
- Use a module, not a class, for static methods.
- Use a class for functions that share settings or a client.
- Use a base class or a `Protocol` for variants of one contract.
- Keep classes stateless. `__init__` only stores the settings and clients that it receives.
- Do not change `self` or an argument. Return a value.
- Do not create settings or clients in a class. Do not use a module-level singleton.
- Use one level of inheritance. Use composition to combine classes.
- Do not write a pipeline class. Pipeline steps are Dagster assets.

Example: `StorageBackend` in `src/srdp/io/storage.py` and its variants in `src/srdp/io/ducklake.py`.

### Settings, secrets and models

- Use a `BaseSettings` class with an `env_prefix` for configuration.
- Put a credential in that class as `Secret[...]`, and set `hide_input_in_errors=True`.
- Use a `BaseModel` for data that moves through the code.
- Make the settings once at the entry point. Pass them to the class in `__init__`.
- Do not read settings or `os.environ` in a class.
- Do not put a credential in `srdp.toml` or in a default value.
- Do not send a plain dict or an untyped dataclass across a boundary.
- Use `Field` constraints and validators, so that bad data fails at the boundary.

Example: `S3StorageSettings` in `src/srdp/io/ducklake.py`.

### Errors and logging

- Raise the most specific exception. Put the bad value in the message.
- Keep the cause with `raise ... from err`.
- Do not use a bare `except:`. Do not return `None` to show a failure.
- Raise `NotImplementedError` for missing code. Do not leave `pass`, `...` or a fake value.
- Catch an error only where you can handle it.
- Log an error one time, where you handle it. Do not log it and raise it again.
- Use `logger.exception()` in an `except` block.
- Use `logging.getLogger(__name__)`. Do not use `print()`.
- Use `%s` arguments in log messages, not f-strings.
- Do not log customer data, secrets or tokens.

### Names, docstrings and comments

- Use clear names, such as `filtered_orders`. Name a function after its action.
- Use named constants, not magic numbers.
- Use `pathlib`, not `os.path`.
- Give each public module, class and function a short Google-style docstring.
- In a docstring, write only what the code does, its arguments, its return value and its errors.
- Do not write motivation, history or rejected options in docstrings or comments. Put them in an ADR or the pull request.
- Write an inline comment only for odd or unclear code. Do not describe what the code does.
- Write a TODO as `# TODO(#<issue>): <what is missing>`. A TODO needs an issue.
- Remove commented-out code.

### Data code

- Use Polars, not pandas.
- Stay lazy. Use `scan_*`, and call `.collect()` only at the end.
- Let DuckDB and DuckLake do the work in the query.
- Use vectorized expressions. Do not loop over rows.
- Use a `set` or `dict` for lookups, not a `list`.
- Use async for I/O that waits, such as HTTP calls.
- Use synthetic example data only.

### SQL

- Do not put user input in SQL.
- Pass values as query parameters.
- Put an identifier in SQL only when it comes from a validated asset key.

### Linter findings

- Fix the code first.
- Use `# noqa: <code>` only for a false positive. Do not use it to relax a rule.
- Write `# ty: ignore[<rule>]` with the rule, and put the reason on the line above.
- Discuss a change to the ruff or ty rules with the team first.

### Code location

- Put reusable platform code in `src/srdp/`.
- Put assets and definitions in `src/srdp/assets/` or `projects/<name>/src/etl/assets/`.
- Give each submodule a clear boundary and interface.
- Put only imports and a docstring in a package `__init__.py` file.

### Tests

- Write the failing test first. Test one behaviour per test.
- Write tests as plain functions. Use fixtures for shared setup.
- Test only our own code.
- Mirror the source tree under `tests/`.
- Keep a test name below 50 characters. Put the conditions in a one-line docstring.
- Use offline, synthetic data only.
- Give each acceptance criterion of an issue at least one test.
