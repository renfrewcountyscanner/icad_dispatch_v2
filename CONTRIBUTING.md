# Contributing to iCAD Dispatch

Thank you for your interest in contributing to iCAD Dispatch!

---

## Code of Conduct

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md). Please be respectful and constructive.

---

## How to Contribute

### Reporting Bugs

1. **Search existing issues** - Check if the bug has already been reported
2. **Create a new issue** - Use the Bug Report template
3. **Include**:
   - Clear description of the problem
   - Steps to reproduce
   - Expected vs actual behavior
   - iCAD Dispatch version
   - Browser/OS information
   - Relevant logs (if applicable)

### Suggesting Features

1. **Search existing feature requests** - Avoid duplicates
2. **Create a new issue** - Use the Feature Request template
3. **Describe**:
   - The problem you're trying to solve
   - Proposed solution
   - Alternative solutions considered
   - Use case (who benefits?)

### Pull Requests

1. **Fork the repository**
2. **Create a feature branch**: `git checkout -b feature/my-feature`
3. **Make your changes** - Follow the coding standards
4. **Add tests** - If applicable
5. **Update documentation** - If needed
6. **Commit with clear messages**: `git commit -m "Add feature: ..."`
7. **Push to your fork**: `git push origin feature/my-feature`
8. **Submit a Pull Request** - Fill out the template

---

## Development Setup

### Prerequisites

- Docker and Docker Compose
- Python 3.12+ (for local development without Docker)
- SQLite

### Local Development

```bash
# Clone the repository
git clone https://github.com/renfrewcountyscanner/icad_dispatch_v2.git
cd icad_dispatch_v2

# Copy environment file
cp .env_example .env

# Edit .env with your settings
nano .env

# Start with local build (development)
docker compose -f docker-compose.production.yml up -d

# View logs
docker logs -f icad_dispatch

# Run migrations (if needed)
# The app runs migrations automatically on startup
```

### Running Tests

```bash
# Run the container in development mode
docker compose -f docker-compose.production.yml up -d --build

# Check logs for errors
docker logs icad_dispatch
```

---

## Coding Standards

### Python

- Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/)
- Use type hints where appropriate
- Add docstrings for new functions
- Keep functions focused and modular

### JavaScript

- Use ES6+ features
- Add JSDoc comments for functions
- Keep code consistent with existing style

### Templates (Jinja2)

- Indent with 4 spaces
- Keep templates clean and readable
- Use appropriate Bootstrap classes

### Git Commit Messages

- Use present tense: "Add feature" not "Added feature"
- Keep first line under 72 characters
- Reference issues where applicable

**Example**:
```
Add user system permissions

- Add is_admin and is_active columns to users table
- Create user_systems table for per-system permissions
- Add admin_required and permission_required decorators
- Update session handling to store user permissions
```

---

## Pull Request Checklist

- [ ] Tests pass (if applicable)
- [ ] Code follows coding standards
- [ ] Documentation updated (if needed)
- [ ] Commit messages are clear
- [ ] PR description explains the changes

---

## Getting Help

- **GitHub Issues**: For bug reports and feature requests
- **Discussions**: For questions and general help

---

## Recognition

Contributors will be listed in the README and/or CHANGELOG.

---

Thank you for contributing!