# Changelog

## 1.0.2 — 2026-09-06

- Isolate the live panel test from the host's `WEZTERM_PANE` environment variable.
- Validate hook payloads and report malformed input without a traceback.
- Explicitly close the CLI's SQLite connection and remove an unused exception binding.
- Ignore local implementation plans and embed the recorded demo in GitHub's native video player.
- Verify all 20 tests both with and without `WEZTERM_PANE`.

## 1.0.1 — 2026-09-05

- Bind panels to their Codex CLI process and preserve tasks after exit. Ignore stale session-end events from an older owner.
- Serialize concurrent panel creation, recover dead creators, and close newly created panels if registration fails.
- Include the MIT license inside the installed plugin directory.
- Add lifecycle, concurrency, process lookup and packaging regressions. Full Windows/macOS lifetime integration remains unverified.

The existing `v1.0.0` tag is unchanged.
