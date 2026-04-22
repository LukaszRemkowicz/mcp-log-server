# MCP Log Server Repository Foundation

## Purpose

This document records the initial repository foundation created in
`mcp-log-server`.

## Scope

This repository currently includes:

- repository structure for the FastMCP service
- local developer bootstrap
- settings/config approach
- testing scaffold
- architecture docs for cross-repo ownership
- Dockerfile and Docker Compose bootstrap for local runs
- `uv` as the package/runtime workflow

This repository does not yet include:

- shared platform decisions for Keycloak and reverse proxy ownership
- final Keycloak/JWT auth integration
- finalized `landingpage` production auth flow
- collector parity tools

## Delivered Files

- `README.md`
- `pyproject.toml`
- `.env.example`
- `doc/mcp_log_server_architecture.md`
- `docker-compose.yml`
- `docker/app/Dockerfile`
- `src/`
- `src/tests/test_settings.py`

## Current State

- stable application skeleton: yes
- clear repo-vs-upstream ownership: yes, in `doc/mcp_log_server_architecture.md`
- Docker-first local runtime: yes
- source manifest contract: yes
- internal auth abstraction with mocked authorization: yes
