# Security

Do not report a suspected malicious plugin in its public issue tracker first. Open a
private GitHub security advisory in this registry or contact the address on Martin's
GitHub profile. Include plugin id, version, digest, evidence, and whether you executed
it.

The catalogue can issue a signed critical revocation which disables one immutable
version in Tine. Reports about ordinary bugs can stay public.

The audit service treats repositories, manifests, lockfiles, build output, PR text,
and source comments as hostile. Builds receive no GitHub, signing, Codex, home, SSH,
or cloud credentials. The Codex review receives bounded source as prompt data and has
all local execution tools disabled. The publisher sees structured reports, not a
checkout, and has a separate narrow GitHub identity and registry signing key.
