"""Top-level Python package for the backend application.

Purpose:
- Provide a stable import root (`app`) for application code and tests.
- Allow tools like mypy and pytest to resolve modules via `-p app`.

Why needed:
- On Windows and with nested folders, explicit packages help avoid duplicate-module
        resolution issues and make imports unambiguous (e.g. `from app.main import app`).

Makes `app` importable
"""

# Marks "app" as a package so tests can `from app.main import app`
