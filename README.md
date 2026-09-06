# Codex Tasklist

A live task list below Codex CLI across terminals. **Version 2.0** adds native integrations and a simple launcher with tmux fallback. Python 3.10+, no extra Python packages.

![Illustrative five-row task list with active, pending and completed tasks](docs/tasklist-preview.svg)

*Illustrative preview with sample tasks.* Active → pending → done. IDs stay fixed; each Codex session has its own saved queue.

## Demo

https://github.com/user-attachments/assets/38b5e887-e444-4f12-92d4-641a7629fbf5

**[Watch the 59-second demo (MP4)](docs/tasklist-demo.mp4)** — type requests naturally; Codex creates tasks and matches follow-ups to existing ones. Includes seven tasks, scrolling, and the full status line.

Real screen recording, time-compressed. The mouse indicator is a recording aid, not part of the plugin.

## Install

Requires **Codex CLI** and **Python 3.10+** (`python3` on Linux/macOS; `python` on Windows PATH).

```sh
codex plugin marketplace add martinille/codex-tasklist
codex plugin add codex-tasklist@codex-tasklist
```

Review and trust the plugin hooks in `/hooks`, then start a new Codex session. Available native panels open automatically.

**On native Windows, keep running `codex` directly.** WezTerm panels open automatically; other native Windows terminals retain the task queue. No launcher or shell-policy changes are needed. For tmux panels in Windows Terminal, use WSL.

For automatic setup on **Linux/macOS/WSL**, ask Codex once:

> Set up the codex-tasklist launcher for me.

Then use **`codex-tasklist` instead of `codex`**. All arguments work the same way, including `codex-tasklist resume`.
The launcher uses an available native integration, or starts a private tmux session with the panel settings already configured. You do not need to learn tmux commands. It does not change your existing tmux configuration or sessions.

If tmux is missing on Linux/macOS/WSL, install it once using your package manager ([installation instructions](https://github.com/tmux/tmux/wiki/Installing)). The launcher never installs software automatically. Without a usable panel, Codex still starts and task commands remain available.

<details>
<summary><strong>Manual launcher setup</strong></summary>

Using the installed script path and data directory from the session's hook, run `install-launcher`. From a local checkout:

```sh
python3 plugins/codex-tasklist/scripts/tasklist.py install-launcher
```

This installs `codex-tasklist` into `~/.local/bin` on Linux/macOS/WSL. Add that directory to PATH if the installer reports it is missing. It refuses to overwrite unrelated files. Re-run setup after upgrading the plugin so the launcher points to the new installed version.

Native Windows does not install a launcher. Use `codex` or `codex resume` directly.

Without installing a command, use `python3 PATH/TO/tasklist.py start -- resume` (Linux/macOS/WSL).

</details>

## Terminal support

| Terminal / environment | Panel route | Requirements / limits |
| --- | --- | --- |
| WezTerm | Native | Existing integration on Linux, macOS and Windows |
| kitty | Native, otherwise launcher fallback | `kitten` CLI, permitted local Unix remote-control socket (`KITTY_LISTEN_ON`), `splits` layout; launch options require a recent kitty |
| iTerm2 on macOS | Native, otherwise launcher fallback | AppleScript automation permission; no Python SDK installation |
| Ghostty on macOS | Native when its API exposes terminal TTYs, otherwise launcher fallback | AppleScript automation permission; older scripting APIs fall back |
| Ghostty on Linux, Terminal.app, GNOME Terminal, Konsole, Alacritty, VS Code terminal | tmux through the launcher | Linux/macOS; Windows terminals through WSL |
| Windows Terminal + WSL | tmux through the launcher | Codex, Python and tmux run inside the same WSL distribution |
| Windows Terminal + native PowerShell/cmd | Queue commands | Automatic native split targeting is not implemented; use WSL or WezTerm for a panel |
| Other terminals | tmux where compatible, otherwise queue commands | Individual terminal compatibility still needs testing |

When already inside tmux, the plugin uses that session and needs no launcher. Existing tmux settings remain yours; mouse support depends on its configuration. Other multiplexers such as Zellij and Screen fall back to queue commands. The launcher does not nest tmux inside them.

**Fallback:** the launcher checks native availability before starting Codex, then uses tmux if available. If a panel fails after Codex is already running, queue commands keep working; the plugin cannot move a running Codex process into tmux. Start again with the launcher to retry. Native WezTerm/kitty candidates must match the current controlling terminal on POSIX; Windows WezTerm requires a known CLI ancestry leading to its GUI or mux host. Ambiguous ownership falls back without splitting an unrelated terminal. No panel is opened by guessing the currently focused window.

## Controls

| Action | Control |
| --- | --- |
| Scroll | Mouse wheel, ↑ / ↓, Page Up / Down |
| Jump | Home / End or click the scrollbar |
| Resize temporarily | Drag the pane divider |
| Save a height | Ask Codex; 1–40 rows, default 5 |
| Close panel | `q`; the next prompt reopens it |

<details>
<summary><strong>Platform checks &amp; known limitations</strong></summary>

| Environment | Verified |
| --- | --- |
| Linux | Queue, ownership, concurrent creation, recovery and packaged CLI tests; live PTY scrolling, resizing and updates |
| Linux + WezTerm / kitty 0.48.2 | Real Codex hooks, task updates, Unicode, scrolling, resizing, focus, reopen and forced owner termination |
| Linux + Konsole + tmux 3.4 | Real Codex through the automatic launcher; updates, scrolling, reopen, focus and private-server cleanup after owner termination |
| Windows + WezTerm | Real Codex hooks, Unicode, keyboard and mouse scrolling, resizing, reopen, exit/resume and forced owner termination |
| Bash / PowerShell without WezTerm | Queue commands and hook output only |
| macOS native adapters | Command construction and targeting tests; not yet tested on a real machine |

- Panels require a live Codex CLI owner and close when it exits, including abrupt termination, or when the session moves to another owner or pane. Real Codex exit/resume and forced termination preserve saved tasks on Linux and Windows.
- Ownership is detected through the nearest Codex CLI ancestor of its hook. Desktop app-server sessions are outside this support contract. macOS lifetime tests remain outstanding.
- Concurrent panel creation is serialized per session without blocking queue writes during terminal calls. A dead creator's reservation is reclaimed on retry; an unregistered panel closes on its next ownership check after its renderer starts. A different live Codex owner must close before that session can be resumed elsewhere.
- Native iTerm2 and Ghostty integrations need live terminal verification before being treated as fully verified. Unsupported API versions and denied automation fall back without deleting tasks.
- iTerm2's AppleScript API is deprecated upstream. It avoids adding a Python SDK dependency; if it stops working, the launcher uses tmux when available.
- The Windows VirtualBox test had low contrast and WezTerm graphics warnings; these remain unresolved.
- Packaging and current Linux/Windows integration checked with Codex 0.153.4. See the [v2 live test report](docs/testing-v2.md) for versions, coverage and remaining platform limits.

</details>

<details>
<summary><strong>Development &amp; storage</strong></summary>

Install from a local checkout:

```sh
codex plugin marketplace add .
codex plugin add codex-tasklist@codex-tasklist
```

Run tests:

```sh
python3 -m unittest discover -s tests
```

With `tmux` on PATH, the suite also runs isolated live tmux tests (updates, resizing, reopening, focus, owner exit and the launcher). Otherwise those tests are skipped locally. CI runs Python 3.10 and 3.13 on Linux, Windows and macOS, and requires tmux on Linux. The launcher preserves the shell's umask and the Codex exit status.

Manual queue example from the repository root:

```sh
python3 plugins/codex-tasklist/scripts/tasklist.py --session demo add "Inspect the issue" --status active
python3 plugins/codex-tasklist/scripts/tasklist.py --session demo update 1 --status done
python3 plugins/codex-tasklist/scripts/tasklist.py --session demo list
```

Use numeric IDs (`1`, not `T01`). On Windows, use `python` instead of `python3`.

Installed queues use `PLUGIN_DATA`; manual commands default to `$XDG_STATE_HOME/codex-tasklist` or `~/.local/state/codex-tasklist`. Use the hook's `--data-dir` to inspect an installed queue. SQLite protects concurrent updates; task titles reject terminal controls. The plugin does not upload queue data to GitHub.

Source: `plugins/codex-tasklist/`. Marketplace: `.agents/plugins/marketplace.json`. Reinstall after source changes and restart Codex to load updated hooks.

</details>

## License

[MIT](LICENSE).
