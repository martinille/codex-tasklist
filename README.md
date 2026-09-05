# Codex Tasklist

A small, persistent user-request queue below Codex CLI in the same WezTerm tab.

The panel opens automatically after its plugin hooks have been trusted. It shows one list: active requests first, pending/blocked requests next, completed requests last. IDs remain stable. Completed entries have a green tick and dimmed, struck-through text; active entries have a yellow arrow. There is no legend. Long queues have a scrollbar.

## Requirements

- Linux, macOS, or Windows 10/11 with Python 3.10+ (standard library only).
- WezTerm with `wezterm cli split-pane` and `adjust-pane-size` support.
- A current Codex CLI supporting plugin lifecycle hooks (packaging checked with 0.153.4; Windows session integration tested with 0.146.0).

No shell replacement, tmux, Node package, API key or patched Codex binary is required. Windows task creation and completion through a real Codex session, automatic panel opening, rendering and keyboard scrolling have been tested. Remaining validation limits are listed below. Outside WezTerm, queue commands work but no panel opens.

On Linux/macOS, Python must be available as `python3`; on Windows, enable the `python` command in PATH during Python installation. Windows hooks use PowerShell and UTF-8 mode. No pip packages, virtual environment, or compilation are required. Codex and the panel must run in the same local WezTerm environment.

## Install from GitHub

After the reviewed version has been pushed to GitHub:

```sh
codex plugin marketplace add martinille/codex-tasklist
codex plugin add codex-tasklist@codex-tasklist
```

`codex-tasklist` before `@` is the plugin name; after `@` it is the marketplace name. Both use the same name so installation is easy to remember. While the GitHub repository is private, installation requires access to it. Public installation is available only after the owner makes the repository public.

For local development before publishing, run from this checkout:

```sh
codex plugin marketplace add .
codex plugin add codex-tasklist@codex-tasklist
```

Review and trust the four plugin hooks in Codex's hook settings. Start a new Codex session from your normal WezTerm shell. The plugin does not change your `codex` command, shell configuration or terminal colors. Private GitHub repositories require repository access before cloning; the code contains no owner-specific paths.

## Usage

Describe your requests normally. The hooks instruct Codex to maintain the queue, and a stop hook reminds it if entries remain active. Task updates are model-driven: the display does not infer completion from prose or tool activity. Always review a suspicious status instead of assuming the hook guarantees correctness.

Each session has its own queue. Resume a session to retain its entries. A new session starts empty. Closing the parent pane or ending the session closes its panel; the stored tasks remain.

Click the list and scroll with the mouse wheel, arrow keys or Page Up/Down. Click the scrollbar to jump; Home/End also work. The initial height is five rows. Resize the pane normally for a temporary change, or ask Codex to set the saved row count. `q` closes the panel; the next user prompt reopens it.

The hook supplies a correctly scoped command prefix. For manual use:

```sh
python3 plugins/codex-tasklist/scripts/tasklist.py --session demo list
python3 plugins/codex-tasklist/scripts/tasklist.py --session demo add "Inspect the import failure" --status active
python3 plugins/codex-tasklist/scripts/tasklist.py --session demo update 1 --status done
python3 plugins/codex-tasklist/scripts/tasklist.py rows 8
```

Installed hooks use `PLUGIN_DATA`. Manual commands default to `$XDG_STATE_HOME/codex-tasklist` or `~/.local/state/codex-tasklist`; use the hook's `--data-dir` to access an installed session. SQLite transactions protect concurrent updates, and task titles cannot contain terminal controls. Task data is local and is never sent to GitHub by this plugin. Runtime queues and preferences do not belong in source control.

## Development

```sh
python3 -m unittest discover -s tests
```

The installable plugin is under `plugins/codex-tasklist/`; marketplace metadata is in `.agents/plugins/marketplace.json`. After changing installed plugin files, reinstall the plugin and start a new session so Codex loads the new hooks. Keep source review separate from commits and publishing.

Version: **1.0.0**. Author: **Martin Ille**. Local version tag: `v1.0.0`. A local tag does not imply publication or completion of the remaining validation. No open-source license has been selected yet; select one before public redistribution.

## Validation and known limitations

- Linux: five automated tests pass, including a live PTY panel with scrolling, resizing and updates.
- Windows: four data/lifecycle tests pass; the POSIX PTY test is skipped. A real WezTerm/Codex session opened the panel and created and completed a task. Twenty-item keyboard scrolling and completed-item styling were checked separately.
- Bash and PowerShell outside WezTerm: queue commands and hook output work, but there is no automatic visual panel.
- Session-end cleanup is implemented and unit-tested, but actual Codex exit/resume cleanup still needs an end-to-end check. Unexpected termination while the parent shell remains open is not covered.
- No real macOS test has been run. Windows mouse scrolling and runtime pane resizing still need verification.
- Sandbox settings may require approval for queue commands. The plugin does not bypass approvals.
- The Windows VirtualBox test environment had low-contrast rendering and graphics warnings in WezTerm; changing the graphics backend did not fully resolve this.
- Task titles follow the current session language. Status and title accuracy depend on the model following the plugin instructions.
