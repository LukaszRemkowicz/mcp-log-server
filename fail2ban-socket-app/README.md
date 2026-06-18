# Fail2ban Socket App

Small standalone app that owns fail2ban access and exposes a fixed set of
read-only fail2ban diagnostics over a Unix domain socket.

The app is intentionally neutral. It does not know about MCP callers, projects,
JWTs, audit rows, manifests, or response shaping.

## Purpose

The app exists to keep the host fail2ban socket and `fail2ban-client` access out
of larger application containers. A consuming app can mount only this app's Unix
socket and ask for a small set of fixed fail2ban reads.

This app is not an HTTP service and does not expose a TCP port.

## Runtime Shape

```text
consumer app
  -> shared Unix socket
  -> fail2ban-socket-app
  -> fail2ban-client -s /var/run/fail2ban/fail2ban.sock
```

The socket path comes from `FAIL2BAN_SOCKET_APP_SOCKET_PATH`. The host fail2ban
socket path comes from `FAIL2BAN_SOCKET_PATH`.

## Protocol

The socket protocol is JSON lines:

- one request per line
- one response per line
- request body must be a JSON object
- `operation` must be a supported fixed operation name
- `params` must be a JSON object

Example request:

```json
{"operation":"get_jail_bans","params":{"jail_name":"portfolio-nginx-probes"}}
```

Example success response:

```json
{"ok":true,"result":{"jail_name":"portfolio-nginx-probes","currently_banned":2,"banned_ips":["1.2.3.4"]}}
```

## Supported Operations

`list_jails`

Returns known fail2ban jails and jail count.

Params:

- none

`get_jail_bans`

Returns banned IP information for one jail.

Required params:

- `jail_name`

`blocked_ips_summary`

Returns banned IPs grouped by jail.

Params:

- none

## Non-Goals

This app must not expose:

- HTTP or TCP ports
- arbitrary shell commands
- arbitrary `fail2ban-client` arguments
- ban, unban, reload, restart, or mutation operations
- caller authentication or authorization logic
- application-specific project or manifest logic

## Development

Run tests:

```bash
uv run pytest
```

Run lint:

```bash
uv run ruff check .
```

Build the container image from the repository root:

```bash
docker build -f docker/fail2ban-socket-app/Dockerfile -t fail2ban-socket-app .
```
