"""Makes `tests` a real package. Do not delete.

Two modules import across the suite — `tests/test_legal.py` reuses
`test_rera.make_page`, and `conftest.py` falls back to `tests._local_pg` — and
CLAUDE.md documents `python -m tests.regen_golden`. All of that needs `tests`
to be importable by name.

Without this file it works locally by accident: the editable install puts the
project root on sys.path. The CI runner's setuptools uses a strict editable
finder that exposes only `atlas`, so the import fails there with
`ModuleNotFoundError: No module named 'tests'`. With `__init__.py` present,
pytest's prepend import mode inserts the repo root (the first ancestor without
an `__init__.py`) instead, which works in both places.
"""
