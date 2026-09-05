# Changelog

## 1.0.1 — prepared snapshot

- Bind panels to their Codex CLI process and preserve tasks after exit. Ignore stale session-end events from an older owner.
- Serialize concurrent panel creation, recover dead creators, and close newly created panels if registration fails.
- Include the MIT license inside the installed plugin directory.
- Add lifecycle, concurrency, process lookup and packaging regressions. Full Windows/macOS lifetime integration remains unverified.

The existing `v1.0.0` tag is unchanged. This snapshot is not tagged or published yet.
