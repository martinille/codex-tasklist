# Changelog

## 2.0.0 — 2026-09-06

- Add tmux panels and native kitty, iTerm2 and compatible Ghostty macOS adapters alongside WezTerm.
- Add the optional Linux/macOS/WSL `codex-tasklist` launcher. It probes native support, then starts a private tmux session with mouse support and no extra status bar when needed. Users do not need to configure tmux.
- Preserve queue commands when a panel is unavailable. A session-start notice explains the fallback without repeating on every prompt; dependencies are never installed automatically.
- Preserve existing task IDs and statuses when migrating session metadata to track terminal backends and panel launch tokens.
- Keep panels bound to their original Codex owner and terminal. Existing multiplexers take priority over inherited outer terminal IDs; unregistered panels exit after failed creation.
- Add adapter, fallback, argument forwarding and launcher installation checks, plus live tmux panel and launcher tests. Verify real Codex sessions with Linux WezTerm, kitty, Konsole/tmux and Windows WezTerm, including exit/resume and forced termination. Native macOS adapters still require live validation; Windows Terminal panels require WSL.
- Recognize kitty when it inherits another terminal's `TERM_PROGRAM`, and correct cell rounding when resizing kitty panels.
- Native Windows runs `codex` directly, without a `.cmd` wrapper or shell-policy changes. Reject launcher setup there with platform-specific guidance, avoiding batch reinterpretation of arguments.
- Preserve Unicode queue output on Windows without requiring UTF-8 environment variables, and reject non-string hook session IDs cleanly.
- Verify native pane ownership against the controlling terminal on POSIX and known WezTerm host ancestry on Windows before splitting.
- Preserve the caller's umask and Codex exit status through the launcher, including Ctrl-C through tmux.
- Restore Ghostty's previous foreground terminal after splitting without activating a background application.
- Add Linux/Windows/macOS CI coverage for Python 3.10 and 3.13; require tmux in Linux jobs before the plugin scanner runs.

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
