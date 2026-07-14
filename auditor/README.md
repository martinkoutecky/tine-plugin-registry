# Local auditor

This service is intentionally not a generic self-hosted GitHub Actions runner. It
polls only this registry, leases immutable submissions, and has three boundaries:

1. `daemon.py` uses unauthenticated read-only GitHub HTTP for this public repository
   and clones the submitted public commit. It has no GitHub credential.
2. Hostile fetch/build runs in a fresh rootless Podman container when Podman is
   available, or in a Bubblewrap namespace with the same fail-closed policy on this
   host. The sandbox has no host home, sockets, Codex auth, GitHub credentials,
   signing key, or publisher spool. Dependency fetch gets network but no credentials;
   submitted build scripts run only in the network-disabled phase. Source is
   read-only and only dedicated cache and artifact directories are writable.
3. `codex_review.py` sends a bounded text bundle to ephemeral `codex exec` with user
   config/rules ignored and shell, unified exec, apps, browser, computer-use, and
   plugins disabled. The model has no source checkout or tool path to credentials.
4. A separately configured publisher consumes structured result envelopes from an
   outgoing spool. It mints one-hour tokens from a GitHub App installed only on
   this registry; its Ed25519
   key signs `index.json` after deterministic canonical serialization. The signed
   index pins separate SHA-256 digests for both the manifest and WASM bytes.

The AI report never overrules deterministic failures. `graph.write.block` and any
review uncertainty quarantine. The service can therefore fail closed unattended.

## Setup

```sh
podman build -t tine-plugin-builder:0.1 auditor/container
cp auditor/config.example.toml ~/.config/tine-plugin-auditor/config.toml
python3 auditor/daemon.py --once --config ~/.config/tine-plugin-auditor/config.toml
python3 -m unittest discover -s auditor -p 'test_*.py' -v
python3 scripts/validate-index.py
```

Podman is preferred. If it is unavailable, install Bubblewrap and verify that
unprivileged user namespaces are enabled. The auditor refuses executable submissions
when neither isolation backend is usable; it never falls back to a host build.

Install the two user-level service/timer files only after assigning a narrow
publisher credential. Never give a public-PR workflow access to this machine or
mount the Podman socket into a build. On a long-running environment without cron
or systemd, use the checked-in supervisor instead; it keeps the two schedules
independent, holds a single-instance lock, survives the launching shell, and writes
credential-free health state:

```sh
cp auditor/supervisor-config.example.toml auditor/supervisor-config.local.toml
python3 auditor/operations.py start --config auditor/supervisor-config.local.toml
python3 auditor/operations.py status --config auditor/supervisor-config.local.toml
```

The supervisor does not invent a boot facility. Where the environment itself is
recreated or rebooted, arrange for the `start` command above to run at login, or use
the systemd timers. A running but stale or failed worker makes `status` exit nonzero.

The publisher GitHub App needs only repository `Contents: read/write` and `Pull
requests: read/write`, and must be installed only on `tine-plugin-registry`.
First choose one persistent private root. It must be outside every Git worktree,
owned by the service user, and mode 700. Container deployments must point this at
a host-backed mount rather than the container's home directory. For Martin's
current deployment:

```sh
export TINE_PLUGIN_PRIVATE_ROOT=/aux/koutecky/logseq/.tine-private/plugin-registry
install -d -m 700 "$TINE_PLUGIN_PRIVATE_ROOT"
```

Use this root for the App key, App metadata, registry signing key, and auditor
state; the helpers create the App files and state directories there. Without the
environment variable, the portable default is
`~/.local/share/tine-plugin-registry`; do not use that default when home is
ephemeral.

The loopback-only manifest helper removes the error-prone key download and App-ID
copying steps:

```sh
python3 auditor/bootstrap_github_app.py
```

Open the printed `127.0.0.1` URL, review and create the private App on GitHub,
then install it using **Only select repositories** with only
`tine-plugin-registry` selected. The helper persists only the App ID/slug and
private key; it discards GitHub's generated client and webhook secrets. The key
and metadata are created atomically as mode 600 and existing files are never
overwritten. After installation, verify the live App identity, permissions, and
installation-token repository scope and create the local configuration with:

```sh
python3 auditor/configure_publisher.py
```

Then schedule `publisher_daemon.py --once` under a separate lock, or start the
supervisor above. It validates the mode-600 configuration and keys every cycle
and creates short-lived installation tokens on demand; there is no PAT to retain
or rotate. Each cycle
atomically updates `$TINE_PLUGIN_PRIVATE_ROOT/state/publisher-status.json`
with only its timestamp and aggregate pending/published/quarantined/failure
counts, so the schedule is observable without logging credentials or source.

For a remote machine, choose a fixed loopback port and forward that same port
over SSH, for example `--port 39401` with an SSH local forward from local
`39401` to remote `127.0.0.1:39401`. The callback remains loopback-only on both
ends of the tunnel.

On GitHub, create the App with no webhook, no user authorization, and no account
permissions. Grant repository `Contents: read/write` and `Pull requests:
read/write`, and install it only on `martinkoutecky/tine-plugin-registry`. The
checked-in cron and systemd examples keep the credential-free auditor and
privileged publisher in separate processes.

The checked-in `registry-ed25519.pub.pem` is the dedicated registry identity. The
private key stays mode-600 at
`$TINE_PLUGIN_PRIVATE_ROOT/registry-ed25519.pem`, outside every repository.
Tine must verify `index.json.sig` before it trusts catalogue metadata or revocations.
