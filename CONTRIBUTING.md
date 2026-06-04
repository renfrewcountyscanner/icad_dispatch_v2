# Contributing to iCAD Dispatch v2

Thank you for your interest in contributing! This guide will help you get started.

## Code of Conduct

Be respectful, constructive, and inclusive. We're all here to help emergency services.

## How to Contribute

### Reporting Bugs

1. Check [existing issues](https://github.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2/issues) first
2. Open a new issue with:
   - Clear title
   - Steps to reproduce
   - Expected vs actual behavior
   - Environment details (OS, Docker version, iCAD version)
   - Relevant log excerpts

### Suggesting Features

1. Open a [GitHub Discussion](https://github.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2/discussions) first
2. Describe the use case (which emergency service, what problem it solves)
3. If there's consensus, open an issue to track implementation

### Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Write or update tests if applicable
5. Update documentation
6. Submit a pull request with:
   - Clear description of changes
   - Link to related issue(s)
   - Screenshots for UI changes

## Development Setup

### With Docker (Recommended)

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2.git
cd icad_dispatch_v2
cp .env.example .env
# Edit .env for local development
docker compose -f docker-compose.production.yml up -d
```

### Without Docker

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Coding Standards

- **Python**: Follow PEP 8
- **JavaScript**: Use consistent formatting (existing style)
- **HTML/CSS**: Use semantic HTML, BEM-like CSS naming
- **Commits**: Use conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`)

## Testing

Before submitting a PR:

```bash
# Syntax check all Python files
find . -name "*.py" -exec python3 -m py_compile {} \;

# Check container builds
docker compose build
```

## Documentation

- Update relevant docs in `/docs/` for any user-facing changes
- Update README.md if adding major features
- Include screenshots for UI changes

## Questions?

- [GitHub Discussions](https://github.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2/discussions)
- [Discord Community](YOUR_DISCORD_INVITE_LINK) (if applicable)

---

*This project is licensed under the [MIT License](../LICENSE).*
