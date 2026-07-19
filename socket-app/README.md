# Socket App

Small standalone app that owns Docker daemon access and exposes a fixed set of
read-only Docker operations over a Unix domain socket.

The app is intentionally neutral. It does not know about any specific caller,
application server, project, manifest, user, JWT, audit table, or snapshot
store.

## Purpose

The app exists to keep `/var/run/docker.sock` out of larger application
containers. A consuming app can mount only this app's Unix socket and ask for a
small set of fixed Docker reads. This app mounts the real Docker socket and uses
the Docker SDK internally.

This app is not an HTTP service and does not expose a TCP port.

## Runtime Shape

```text
consumer app
  -> shared Unix socket
  -> socket-app
  -> Docker SDK
  -> /var/run/docker.sock
```

The socket path defaults to:

```text
/run/socket-app/gateway.sock
```

The socket file is created by `socket-app`. A consuming app only needs
the shared directory that contains that socket file.

## Protocol

The socket protocol is JSON lines:

- one request per line
- one response per line
- request body must be a JSON object
- `operation` must be a supported fixed operation name
- `params` must be a JSON object

Example request:

```json
{"operation":"container_health","params":{"container_name":"app-1"}}
```

Example success response:

```json
{"ok":true,"result":{"container_name":"app-1","running":true}}
```

Example error response:

```json
{"ok":false,"error":{"message":"Unsupported docker socket operation: run_shell"}}
```

The process writes JSON Lines lifecycle and request outcome events to stdout.
Request logs contain only bounded protocol metadata such as the operation name,
success flag, and duration; request params and response bodies are not logged.

## Supported Operations

`service_health`

Pings the Docker daemon through the Docker SDK connection used by the socket
app. This operation remains available for explicit diagnostics, but Compose no
longer runs a separate startup health gate or recurring socket probe.

Params:

- none

`container_logs`

Returns bounded timestamped logs for one container.

Required params:

- `container_name`

Optional params:

- `since`
- `until`
- `tail`

`container_logs_page`

Creates one immutable Docker-log spool and returns it as lossless base64 pages.
Continuation calls use the returned opaque `transfer_id` and exact
`next_offset`; Docker is queried only while creating the spool. The spool is
deleted after the final page or after its inactivity TTL. A transfer exceeding
`MAX_LOG_TRANSFER_BYTES` fails explicitly and its partial spool is deleted.
The default total-transfer cap is 268435456 bytes (256 MiB); each returned page
remains capped separately at 1000000 bytes.

`container_health`

Returns bounded runtime state for one container.

Required params:

- `container_name`

`container_detail`

Returns sanitized inspect-style metadata for one container.

Required params:

- `container_name`

`container_path_stat`

Returns file or directory metadata for one absolute container path.

Required params:

- `container_name`
- `path`

`container_file_read`

Reads one bounded regular file inside a container.

Required params:

- `container_name`
- `path`

Optional params:

- `max_bytes`

`container_directory_list`

Lists one bounded directory or returns one file metadata entry.

Required params:

- `container_name`
- `path`

Optional params:

- `max_entries`

`vps_containers_inventory`

Returns bounded and redacted visible container inventory.

Params:

- none

`vps_volumes_inventory`

Returns bounded and redacted visible volume inventory.

Optional params:

- `dangling_only`
- `anonymous_only`
- `name_prefix`

## Non-Goals

This app must not expose:

- HTTP or TCP ports
- arbitrary shell commands
- arbitrary Docker API calls
- Docker mutation operations
- image, network, volume, or container changes
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
docker build -f docker/socket-app/Dockerfile -t socket-app .
```

Run locally with the default socket path, or override it for an ad hoc local
session:

```bash
SOCKET_APP_SOCKET_PATH=/tmp/socket-app.sock \
  uv run python -m socket_app.main
```

Send a request:

```bash
python -c 'import socket; s=socket.socket(socket.AF_UNIX); s.connect("/tmp/socket-app.sock"); s.sendall(b"{\"operation\":\"vps_containers_inventory\",\"params\":{}}\n"); print(s.recv(4096).decode(), end="")'
```
