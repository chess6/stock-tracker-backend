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

* Admin API routes are **unauthenticated by default** — set `ADMIN_API_KEY` before exposing the API beyond localhost.
* Never commit `.env`, API keys, or `data/*.sqlite3` files.
* `SEC_USER_AGENT` must be a real contact email per [SEC fair access](https://www.sec.gov/os/webmaster-faq#code-support).

## Safe defaults for public deployments

If you deploy this API publicly:

1. Set `ADMIN_API_KEY` and require `X-Api-Key` on admin routes.
2. Run behind a reverse proxy with TLS and rate limiting.
3. Do not expose the SQLite file or worker shell scripts to untrusted networks.
