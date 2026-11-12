# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| `master` branch | Yes |

Older tags and forks are best-effort only.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report privately via [GitHub Security Advisories](https://github.com/chess6/stock-tracker-backend/security/advisories/new)
for this repository.

Include:

* Description of the issue and potential impact
* Steps to reproduce (proof-of-concept if available)
* Affected paths, endpoints, or configuration

We aim to acknowledge reports within **7 days** and will coordinate disclosure once a fix is available.

## Scope notes

This project is intended for **local development**:

* Admin API routes are **loopback-only** when `ADMIN_API_KEY` is unset. Set `ADMIN_API_KEY` before exposing the API beyond localhost, and always behind a reverse proxy.
* Never commit `.env`, API keys, or `data/*.sqlite3` files.
* Send the admin key only in the `X-Api-Key` header. Keys in URLs end up in access and proxy logs, so query-string keys are rejected.

## Secret scanning

Secrets are checked at three points:

1. **Before each commit:** `.githooks/pre-commit` runs [gitleaks](https://github.com/gitleaks/gitleaks) on staged changes. Enable it once per clone with `brew install gitleaks && git config core.hooksPath .githooks`.
2. **On every push and pull request:** `.github/workflows/secret-scan.yml` scans the full git history and fails the check if anything is found.
3. **GitHub push protection:** rejects pushes that contain secrets from known providers.

Rules, including Nasdaq Data Link and Tiingo key formats, live in `.gitleaks.toml`. Run `gitleaks git --redact .` to scan locally. If a secret is ever committed, **rotate it first**; removing it from history does not make it safe again.
* `SEC_USER_AGENT` must be a real contact email per [SEC fair access](https://www.sec.gov/os/webmaster-faq#code-support).

## Built-in protections

* **CORS allowlist** (`CORS_ORIGINS`): only listed browser origins can read API responses. State-changing requests and all `/api/admin/*` calls carrying another `Origin` are rejected with `403`, so a malicious page cannot drive a local API from your browser.
* **DNS rebinding:** without `ADMIN_API_KEY`, admin routes also require a local `Host` header (`localhost`, `127.0.0.1`, `::1`, or `ALLOWED_HOSTS`). The orchestrator API (port 5001) applies the same Host and Origin checks (`ORCHESTRATOR_ALLOWED_HOSTS`, `ORCHESTRATOR_CORS_ORIGINS`).
* **Outbound fetches** of feed and article URLs (`app/services/safe_fetch.py`) allow only http(s), refuse private, loopback, link-local and other non-public addresses on every redirect hop, cap size and total time, and accept only HTML for article pages.
* **Feed sanitization:** items with non-http(s) links are dropped, and markup (including entity-encoded markup) is stripped from titles and text.
* **Input validation:** numeric query parameters return `400` instead of `500` and are clamped to safe ranges. Ticker lists are type-checked and normalized before anything is written.
* The Flask debugger is off unless `FLASK_DEBUG=1`.

## Owner sign-in (public demos)

Set `APP_PASSWORD_HASH` (generate it with `python -m app.auth hash-password`) to run a public, read-only demo:

* Anyone can browse with GET requests.
* `POST`, `PUT`, `PATCH` and `DELETE` need the owner session from `POST /api/auth/login`. Without it they return `401` with `{"authRequired": true}`.
* The owner session also unlocks `/api/admin/*`. `ADMIN_API_KEY` and loopback access keep working for scripts.
* Sessions are signed, HttpOnly, `SameSite=Lax` cookies that last 7 days. Set `SESSION_COOKIE_SECURE=1` behind HTTPS.
* Login allows 5 failed attempts per address per 15 minutes.
* `GET /api/status/freshness` exposes only data timestamps, for the UI badges.

## Safe defaults for public deployments

If you deploy this API publicly:

1. Set `ADMIN_API_KEY`. Admin routes then require the `X-Api-Key` header.
2. Run behind a reverse proxy with TLS and rate limiting.
3. Do not expose the SQLite file or worker/scheduler shell scripts to untrusted networks.
