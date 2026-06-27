# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 2.x | ✅ Yes |
| 1.x | ❌ No (upgrade to 2.x) |

## Reporting Vulnerabilities

**Please do not open public issues for security vulnerabilities.**

Instead, report privately:

1. Use [GitHub Private Vulnerability Reporting](https://github.com/renfrewcountyscanner/icad_dispatch_v2/security/advisories/new)

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if known)
- Your contact information for follow-up

### Response Timeline

| Phase | Timeline |
|-------|----------|
| Acknowledgment | Within 48 hours |
| Initial assessment | Within 7 days |
| Fix released | Within 30 days (critical), 90 days (non-critical) |
| Public disclosure | After fix is released |

## Security Best Practices for Deployments

See the full [Security Hardening Guide](docs/security.md).

### Minimum Requirements

- HTTPS in production
- Strong secrets (`PUBLIC_MAP_API_KEY`, `MAP_SECRET_KEY`, `PG_PASSWORD`)
- Firewall (only 22, 80, 443 open)
- Regular updates
- `.env` file permissions set to `600`

### What NOT to Do

- ❌ Expose PostgreSQL port (5432) to the internet
- ❌ Use default passwords
- ❌ Commit secrets to Git
- ❌ Run as root without reason
- ❌ Disable authentication in production

## ⚠️ Repository is Public — Never Commit Secrets

This repository is **publicly visible on GitHub**. Under no circumstances should the following files be committed:

| File | Why |
|---|---|---|
| `.env` | Contains real passwords, API keys, and secrets |
| `.env.local` | Local override with real secrets |
| `.env.production` | Production secrets |
| `var/secret_key.txt` | Auto-generated Flask secret key |
| `*.pem`, `*.key`, `*.p12` | TLS certificates and private keys |

Note: `docker-compose.production.yml` is safe to commit — all secrets reference `${VAR}` from `.env`. No real credentials are stored in it.

### If You Accidentally Commit a Secret

1. **Immediately rotate the exposed credential** (change password, revoke API key, etc.)
2. **Purge from git history**: `git filter-branch --force --index-filter 'git rm --cached --ignore-unmatch <file>' --prune-empty --tag-name-filter cat -- --all`
3. **Force push**: `git push --force origin main`
4. **Verify on GitHub** that the file no longer appears in commit history

### Safe Files to Commit

- `.env.example` — Safe placeholder values only
- `docker-compose.yml` — Generic development config (no secrets)
- Application code, templates, documentation, migrations

## Known Security Considerations

### Public Map is Intentionally Open

The public map is designed to be publicly accessible. It displays the same information broadcast over public emergency radio frequencies. No sensitive personal data is shown.

### Container Runs as Root

Containers run as a non-root user (icad_dispatch, UID 9911) via gosu entrypoint. Volume mounts must be owned by UID 9911 (see install scripts).

### Rate Limiting

Built-in rate limiting protects against abuse:
- 60 requests per minute per IP on public endpoints
- Consider adding reverse proxy rate limiting for additional protection

## Security Updates

Security fixes are released as patch versions (e.g., `2.1.1`). Subscribe to repository notifications to receive alerts.

---

*Last updated: 2026-06-04*
