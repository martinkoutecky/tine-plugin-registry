# Registry policy

- Public source, immutable commit, recognized SPDX license, explicit capabilities,
  explicit supported platforms, and AI-development provenance are required.
- Published `id@version` bytes and SHA-256 are immutable.
- API 0.1 plugins may not require network telemetry or Tine telemetry.
- The deterministic import check must show exactly `env.memory`; the ABI exports are
  `tine_alloc`, `tine_handle`, and `tine_result_len`.
- Low-risk means no graph-write capability. Deterministic pass + reproducible build +
  AI pass without uncertainty can auto-publish.
- `graph.write.block`, source/binary mismatch, reviewer uncertainty, prompt-injection
  behavior, unusual build behavior, or any failed check quarantines automatically.
- Malicious versions are rejected; already-published unsafe versions are revoked in
  the signed index. Revocation records are immutable append-only entries.
- “Community” does not imply third-party authorship or core commitment. Martin may
  publish a community plugin without promising to adopt or maintain it in core.
