# MCP Log Server Repository Foundation

## Purpose

This document records the initial repository foundation created in
`mcp-log-server`.

## Scope

This repository currently includes:

- repository structure for the FastMCP service
- local developer bootstrap
- settings/config approach
- automated HTTP integration tests
- in-memory FastMCP client tests
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
- `infra/docs/current_project_state.md`
- `infra/docs/analysis/mcp_log_server_architecture.md`
- `docker-compose.yml`
- `docker/app/Dockerfile`
- `src/`
- `src/tests/`

## Current State

- stable application skeleton: yes
- phase 1 ready: yes
- clear current project reference: yes, in `infra/docs/current_project_state.md`
- planning/development direction retained separately: yes, in `infra/docs/analysis/mcp_log_server_architecture.md`
- Docker-first local runtime: yes
- source manifest contract: yes
- FastMCP JWT verification with per-request component auth: yes
