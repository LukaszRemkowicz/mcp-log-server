# Copilot Instructions

This repository is a Dockerized Python MCP service with source code under
`src/`.

Important guidance for reviews and code suggestions:

- Prefer small, targeted changes over broad rewrites.
- Keep runtime code simple and explicit. Avoid unnecessary wrappers.
- Treat this repository as a service, not a CLI application.
- Keep tests inside `src/tests/`.
- Prefer environment variables injected externally; do not add hidden config
  layers.
- Keep auth behind internal abstractions so the final provider can switch to
  Keycloak later.
- Preserve the current flat `src/` layout unless explicitly asked to change it.
- Favor Docker/Compose-compatible changes and keep `uv` as the Python workflow.
- When suggesting architecture changes, respect the separation between:
  - Traefik as edge routing
  - Keycloak as platform auth
  - `mcp-log-server` as the application service
