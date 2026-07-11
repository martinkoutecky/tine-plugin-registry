# Tine community plugin registry

Public catalogue, immutable version metadata, audit reports, quarantine decisions,
and signed revocations for [Tine](https://github.com/martinkoutecky/tine)'s
capability-limited WebAssembly plugins.

Human-written, AI-assisted, and AI-primary plugins are equally welcome. AI provenance
is disclosed but is not a security score. Ordinary plugins have no ambient network,
filesystem, process, DOM, browser, Tauri, or graph-path access.

## Submit a version

1. Publish source under a recognized open-source license and commit `Cargo.lock`.
2. Build from a clean checkout and run Tine's `plugin:check` command.
3. Add one immutable `submissions/<plugin-id>/<version>.json` file in a pull request.
4. GitHub performs metadata-only intake. It does not execute pull-request code.
5. Martin's local auditor checks out the exact commit, builds it in a fresh rootless
   Podman sandbox with no secrets, runs deterministic checks, and asks a no-tools
   Codex process for a structured source review.
6. Passing low-risk versions may publish automatically. Anything uncertain or with
   graph-write authority quarantines for explicit review.

Every public version links its deterministic and AI reports. Passing is evidence, not
a guarantee. The signed index pins separate SHA-256 digests for each manifest and
WASM artifact, and Tine checks both before installation. See [POLICY.md](POLICY.md), [SECURITY.md](SECURITY.md), and
[`auditor/README.md`](auditor/README.md).
