# Local auditor

This service is intentionally not a generic self-hosted GitHub Actions runner. It
polls only this registry, leases immutable submissions, and has three boundaries:

1. `daemon.py` uses unauthenticated read-only GitHub HTTP for this public repository
   and clones the submitted public commit. It has no GitHub credential.
2. Hostile fetch/build runs in fresh rootless Podman containers (container root is
   still the unprivileged host user) with no host home,
   sockets, Codex auth, GitHub credentials, signing key, or publisher spool. Build
   scripts run only in the network-disabled phase.
3. `codex_review.py` sends a bounded text bundle to ephemeral `codex exec` with user
   config/rules ignored and shell, unified exec, apps, browser, computer-use, and
   plugins disabled. The model has no source checkout or tool path to credentials.
4. A separately configured publisher consumes structured result envelopes from an
   outgoing spool. Its GitHub identity can update only this registry; its Ed25519
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

Install the two user-level service/timer files only after assigning a narrow
publisher credential. Never give a public-PR workflow
access to this machine or mount the Podman socket into a build.

The checked-in `registry-ed25519.pub.pem` is the dedicated registry identity. The
private key stays mode-600 outside every repository. Tine must verify `index.json.sig`
before it trusts catalogue metadata or revocations.
