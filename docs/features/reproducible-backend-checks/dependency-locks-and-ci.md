# Dependency Locks and Backend CI

## Dependency inputs and install files

- `requirements.in` retains the application's direct dependency ranges.
- `requirements-dev.in` adds test, lint, and lock-maintenance tools.
- `requirements.txt` pins the full runtime graph with SHA-256 hashes.
- `requirements-dev.txt` pins the full development graph, constrained to the
  exact same runtime versions.

The initial pins preserve the existing virtual environment that passed the
backend regression suite. Resolution is performed under Python 3.12 and validated
against the runtime image; it does not select newer package versions by default.
No local environment file is included in lock generation. Local development and
Compose keep their existing requirements filenames.

Install with the existing virtual environment:

```bash
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
```

Docker installs `requirements.txt` with hash verification. These locks target
Linux/Python 3.12, matching the application image and CI. Compile under that
interpreter; another platform/Python version needs its own resolver validation.
See the [pip-tools environment guidance](https://pip-tools.pypa.io/en/stable/).

## Updating a lock

Use Python 3.12 with the pinned pip-tools version. Keep existing pins unless an
upgrade is intentional; review the resulting dependency diff and rerun CI.

```bash
python -m piptools compile --generate-hashes --allow-unsafe --strip-extras --no-annotate -o requirements.txt requirements.in
python -m piptools compile --generate-hashes --allow-unsafe --strip-extras --no-annotate -c requirements.txt -o requirements-dev.txt requirements-dev.in
```

Development tooling includes a pinned pip version because pip-tools requires it;
unsafe packages are explicitly pinned/hashed rather than implicitly resolved.
No dependency update changes application APIs or deploys an existing environment.

## CI execution

`.github/workflows/backend-checks.yml` runs on feature pushes, pull requests to
`dev`/`main`, pushes to `dev`/`main`, and manual dispatch. It uses pinned action revisions and
read-only repository permissions. The job:

1. Installs the hashed development lock on Python 3.12.
2. Runs Ruff and builds the runtime Docker image.
3. Runs the full suite against disposable PostgreSQL and Redis services,
   including real prefork worker tests.
4. Parses JUnit output and rejects missing, skipped, or failed required integration
   suites. Missing service configuration cannot silently produce a green check.
5. Applies Alembic migrations to the disposable CI database and checks API startup,
   OpenAPI availability, metadata registration, and explicit Celery task routes.

Provider tests use substitutes and CI supplies no production credentials.
`/health` and all application API contracts remain unchanged.

Local equivalent after supplying isolated test-service URLs:

```bash
.venv/bin/python -m pytest -q --junitxml=ci-results.xml
.venv/bin/python scripts/check_ci_results.py ci-results.xml
```

The `backend` check is the required branch protection gate. Only passing `main` push builds deploy; see [continuous delivery](../continuous-delivery/main-only-deployment.md).
