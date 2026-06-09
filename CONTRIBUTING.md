# Contributing to Stock Tracker Backend

Thank you for your interest in contributing. This project pairs with the [frontend repo](https://github.com/chess6/stock-tracker-frontend).

## Before you start

1. Read [README.md](README.md) for architecture and setup.
2. Copy `.env.example` to `.env` and set `SEC_USER_AGENT` (never commit `.env`).
3. Keep changes **small and scoped** — match existing patterns in `app/services/`, `app/routes/`, and `app/tests/`.

## Development setup

```bash
pip install -r requirements.txt
cp .env.example .env   # edit SEC_USER_AGENT
sh start.sh
sh worker.sh           # optional — background ingestion
```

## Pull requests

1. Fork and create a branch from `master`.
2. Make your change with tests when behavior changes.
3. Run the test suite:

   ```bash
   timeout 300 python -m pytest app/tests/ -q --maxfail=1 -x
   ```

4. Open a PR using the template. Describe **why** the change is needed and how you verified it.

## Code guidelines

* **Free-first** — prefer SEC, RSS, Stooq, and SQLite cache; avoid new paid API dependencies.
* **SQLite-safe** — batch writes, idempotent upserts, tolerate partial failures.
* **API contracts** — keep JSON shapes stable unless migrating intentionally.
* **Security** — no secrets in code; structured JSON errors from API routes.
* **Tests** — mock external HTTP; cover upserts, route contracts, and job queue behavior.

## Reporting bugs

Use the [bug report issue template](.github/ISSUE_TEMPLATE/bug_report.yml). Include steps to reproduce, expected vs actual behavior, and relevant logs (redact secrets).

## Security

Do **not** open public issues for vulnerabilities. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the
[Apache License 2.0](LICENSE).
