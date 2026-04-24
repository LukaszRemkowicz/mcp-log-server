# AGENTS.md

## Purpose

This file is the local working guide for the `mcp-log-server` repository.

Use it to understand:

- what this repository owns
- how the project is currently structured
- which MCP surfaces are already implemented
- how workflow prompts, skills, and deterministic tools are expected to work

This file is intentionally practical. It is not the full architecture source of
truth. For broader direction, also read:

- `README.md`
- `infra/docs/current_project_state.md`
- `infra/docs/repository_foundation.md`


## Short Project Summary

This repository is building a dedicated FastMCP server that will gradually take
over deterministic log-collection and log-analysis support responsibilities
currently living around the `landingpage` collector and monitoring flow.

Important boundary:

- deterministic code gathers facts
- the LLM interprets facts

The MCP server is not meant to replace deterministic collection logic with free
LLM behavior.


## Current Repository Structure

Top-level directories:

- `src/`
  Main Python application code.
- `docker/`
  Docker image definition for the MCP app.
- `infra/`
  Repository-level infra and foundation notes.

Important current Python files:

- `src/main.py`
  Local entrypoint. Runs the real FastMCP HTTP server.
- `src/app.py`
  Creates the FastMCP app, attaches JWT auth, and imports MCP modules.
- `src/settings.py`
  Environment-backed runtime settings.
- `src/auth/`
  JWT auth provider wiring and scope constants.
- `infra/scripts/generate_dev_jwt.py`
  Local development JWT generator.
- `src/manifests/`
  Manifest schema, loader, and bundled manifest data.
- `src/utils/assets.py`
  Loader for copied workflow assets under `src/agent_assets/`.
- `src/prompts/workflow.py`
  Prepared workflow prompt assembly.
- `src/skills/workflow.py`
  Workflow skill inventory and resource URIs.
- `src/resources/workflow.py`
  MCP resources for reading workflow skill content on demand.
- `src/tools/workflow.py`
  Workflow bootstrap tool.
- `src/tools/system.py`
  MCP service diagnostics tools.
- `src/tests/`
  Current tests for manifests, MCP tools/resources, JWT-protected HTTP API
  behavior, and in-memory FastMCP client behavior.

Copied workflow assets:

- `src/agent_assets/prompts/`
- `src/agent_assets/skills/`
- `src/agent_assets/schemas/`
- `src/agent_assets/examples/`
- `src/agent_assets/tools/`

These assets were copied from `landingpage/backend/monitoring/agent_assets/`
and are the basis for mirroring the existing workflow behavior.


## Current MCP Surface

### Prompts

There is currently no first-class MCP prompt surface that the workflow depends on.

Design intent:

- keep workflow bootstrap structured
- keep prompt preparation separate from tool implementation code
- let the agent fetch prompt text, skill inventory, and tool inventory from one
  structured MCP tool call
- let the agent fetch only the skill resources it needs later

### Tools

Currently implemented:

- `analyze_daily_log_bundle`
- `get_mcp_service_status`
- `get_mcp_health_check`

Purpose:

- `analyze_daily_log_bundle`
  returns structured workflow bootstrap data for the daily log-analysis flow:
  prepared prompt text, skill inventory, and visible tool inventory
- `get_mcp_service_status`, `get_mcp_health_check`
  bootstrap/development diagnostics

Important:

- tools perform actions or prepare structured workflow data
- the LLM should not receive all skill content up front

### Resources

Resources are now a real part of the workflow surface for read-only skill
content.

Current pattern:

- tools prepare workflow bootstrap or perform deterministic actions
- resources expose on-demand skill content
- prompts are prepared in code and returned inside the workflow bootstrap tool

Current workflow skill resources are concrete resources such as:

- `skill://workflow/project_context`
- `skill://workflow/severity_guide`
- `skill://workflow/bot_detection`
- `skill://workflow/recommendations_guide`

`resources/templates/list` is currently expected to return an empty list because
the skill inventory is registered as fixed concrete resources.


## Manifest Model

The source manifest describes what log sources exist for a project and how MCP
should think about them.

Current file:

- `src/manifests/landingpage.json`

Current code:

- `src/manifests/models.py`
- `src/manifests/loader.py`

Manifest purpose:

- inventory of available project sources
- foundation for future deterministic collection tools
- not the same thing as workflow prompts

Simple distinction:

- manifest = what exists
- prompt/workflow = how the daily agent should think and operate


## Workflow Model

This repository should mirror the `landingpage` workflow pattern for daily log
analysis.

That means:

- one main workflow prompt
- separate on-demand skills
- deterministic tool/data retrieval outside the LLM

Do not collapse everything into one huge prompt bundle unless there is a very
clear reason.

Why:

- the `landingpage` workflow intentionally uses on-demand skill loading to save
  tokens
- that same principle should apply here


## Expected Agent Flow

For the daily workflow agent, the intended sequence is:

1. call `tools/call` for `analyze_daily_log_bundle`
2. send the prepared prompt plus skill inventory plus deterministic job context
   to the LLM
3. if the LLM wants a skill, call `resources/read` for the returned
   `skill://workflow/{skill_name}` URI
4. send the next LLM request with:
   - the main prompt
   - only the selected skill resource text(s)
   - deterministic log findings or later MCP-collected log data

Important distinction:

- generic MCP clients like Codex may start with discovery (`prompts/list`,
  `tools/list`)
- the fixed workflow agent should call the known workflow bootstrap tool


## What Is Not Done Yet

Major missing pieces:

- real collector-parity log tools such as `collect_logs`
- snapshot inventory and snapshot lifecycle tools
- real JWT/Keycloak-backed auth
- final cross-repo rollout/integration behavior

Current auth state:

- FastMCP verifies JWTs per request
- component access is enforced with per-component `auth=` checks
- local development uses real example JWTs signed with the local shared secret
- local development JWTs currently expire after 24 hours
- generate fresh local example tokens with:
  `uv run python infra/scripts/generate_dev_jwt.py`
- if you want to save generated tokens in a private local file such as
  `.agent/DEV_JWT_TOKENS.json`, treat that as developer-local state, not as a
  repository contract


## Working Rules For This Repo

- Prefer matching the existing `landingpage` monitoring workflow before
  inventing new abstractions.
- Keep prompts small enough that on-demand skills still matter.
- Do not assume the agent runtime magically understands prompts/tools/skills;
  code still has to orchestrate MCP calls explicitly.
- Treat deterministic collection logic as the primary MCP responsibility.
- Treat workflow prompts as the LLM-facing layer built on top of deterministic
  facts.
- If docs and code diverge, code is the current truth, then update docs.


## Validation

Current local validation command:

```bash
uv run pytest
```

Common run path:

```bash
cd src
uv run python -m main
```

Docker development note:

- Docker Compose mounts `./src` into `/app/src`, so copied prompt/skill assets
  under `src/agent_assets/` are part of the live app volume.
- The compose app now watches the whole `/app/src` tree, not only `.py` files,
  so edits to prompt/skill/schema assets should also trigger reloads.
