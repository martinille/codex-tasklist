# v2 live test report

Test date: 2026-09-06. Version: 2.0.0, unreleased working tree.

Tests use isolated plugin installations, data directories and real Codex CLI processes. No user queues, existing terminal sessions or global tmux configuration are modified. The Linux GUI terminals run on a separate Xvfb display. Windows tests run in the supplied Windows 11 VM.

## Environments

| Environment | Versions | Result |
| --- | --- | --- |
| Linux WezTerm | WezTerm 20260905-195314-b99b1ca2; Codex 0.153.4 | Passed |
| Linux kitty | kitty 0.48.2; Codex 0.153.4 | Passed after detection and resize fixes |
| Linux Konsole with automatic launcher | tmux 3.4; Codex 0.153.4 | Passed |
| Windows 11 WezTerm | WezTerm 20240203-110809-5046fc22; Python 3.13.15; Codex 0.153.4 | Passed |
| Native Windows direct `codex` | Codex 0.153.4; Python 3.13.15 | Passed; no wrapper installed or invoked |
| macOS native integrations / WSL | Not available in the test machines | Not live verified |

The initial Windows Codex 0.146.0 could not run the configured model. A separate official 0.153.4 installation, including its code-mode host, was used without replacing the user's CLI. SSH environment variables were removed from GUI launch scripts: the plugin deliberately refuses native panel targeting in an SSH session.

## Real Codex and panel checks

- Real SessionStart and UserPromptSubmit hooks open panels. Codex adds and completes a task through the installed plugin CLI.
- WezTerm and kitty native panels, plus Konsole/tmux, run concurrently against the same data directory with separate session queues.
- Unicode titles (`Živý test — 世界`) render and update. Twenty-five saved tasks exercise scrolling and retention.
- Linux WezTerm: Home/End, Page Down, mouse-wheel VT input, live height changes, `q`, next-prompt reopen, `/exit`, resume of the same session, and `SIGKILL` of its actual Codex owner.
- Linux kitty: Home/End, `q`, repeated hook reopening, preserved focus, exact requested heights 2/5/8/12, and `SIGKILL` of its actual Codex owner.
- Konsole/tmux: automatic private server with real Codex, task updates, Unicode, End, `q`, hook reopening and preserved focus. Killing the actual Codex owner also removes the panel and private tmux server.
- Windows WezTerm: Home/End, Page Down, mouse-wheel VT input, exact heights 3/5/8, `q`, next-prompt reopen, `/exit`, resume and forced termination of the actual Codex owner. All 25 tasks survive; the unrelated original pane remains open.

Keyboard and mouse sequences are injected through terminal control APIs; these checks exercise the renderer's actual input path. They do not constitute physical mouse or touchpad testing.

## Defects found

1. **Fixed:** kitty inherits `TERM_PROGRAM=WezTerm` but sets `TERM=xterm-kitty`. Detection now recognizes the active kitty terminal despite that inherited value, with a regression case.
2. **Fixed:** kitty converts resize cells to a fractional split bias and can miss the requested height by one row. The adapter checks actual pane rows and performs bounded corrections. Real repeated resizing and a regression test cover this behavior.
3. **Fixed after the user approved direct Windows startup:** Windows PowerShell passed `literal&value` through the `.cmd` launcher as shell syntax. The native Windows wrapper and its argument-forwarding path have been removed. Windows users run `codex` directly; `start` and `install-launcher` return clear guidance without creating files or executing forwarded arguments. The launcher remains available on Linux/macOS/WSL.

## Repeatable regression suite

```sh
python3 -m unittest discover -s tests
```

Final audit-fix run: Linux passed all 41 tests, including real tmux and PTY tests. Windows ran 41 tests: 30 passed and 11 POSIX/tmux-only tests were skipped, without global UTF-8 mode. Earlier runs also verified isolation from inherited outer `TMUX` / `TMUX_PANE` values. The final suite includes a regression ensuring Windows creates no wrapper and never resolves an executable for forwarded arguments. Additional real Windows CLI checks used a temporary Unicode path to verify both rejected launcher commands leave no files, fallback guidance recommends direct `codex`, a saved Unicode queue remains intact, and the native Codex executable starts successfully.

The live setup and checks are retained locally under `/tmp/tasklist-v2-live`; Windows test files use `C:\Users\vboxuser\codex-tasklist-v2-live`. These paths are test artifacts, not dependencies of the plugin or public reproducibility guarantees.

Native iTerm2 and Ghostty have command/targeting tests but no real macOS test. WSL, Terminal.app, GNOME Terminal, Alacritty and VS Code have not individually completed the real Codex workflow in this run. No claim of universal terminal compatibility is made.

## Pre-release audit fixes

All seven audit findings have code changes and regression coverage:

1. CLI stdout/stderr and terminal subprocess decoding use UTF-8 explicitly. Windows queue listing is tested under a legacy `cp1250` output setting and without global UTF-8 mode.
2. WezTerm/kitty pane candidates are checked against the controlling terminal on Linux/macOS. Windows rejects unknown terminal ancestry and accepts both verified WezTerm GUI and mux-server hosts. A real Konsole session carrying valid outer kitty markers chose its own tmux server; the outer kitty panes were unchanged.
3. Ghostty saves the previously focused foreground terminal and restores it after splitting only while the application remains foreground. Focus errors do not discard the created child ID. This has source/API and command-construction validation; live macOS validation remains outstanding.
4. `start` dispatch happens before the task-storage umask change. Both direct startup and actual tmux startup preserve the caller's `022`; queue files remain private.
5. The private tmux child records Codex's exit status. Real PTY tests cover success, nonzero exit and Ctrl-C, while existing tests retain no-double-start and cleanup checks.
6. CI defines Linux/Windows/macOS jobs for Python 3.10 and 3.13, explicitly installs/requires tmux on Linux, and gates the scanner on the tests. Workflow YAML was checked locally; hosted jobs have not run because this working tree has not been published.
7. Hook session IDs require a string before regex validation; malformed numeric/list/object/boolean IDs produce a handled error.

Real Codex hooks were repeated successfully after ownership changes in Linux WezTerm, kitty, Konsole/tmux and Windows WezTerm. Windows was launched without `PYTHONUTF8=1`. The additional controlling-PTY regression checks a hook-like process whose stdout is captured through a pipe.

Test sessions were closed after verification. The nested tmux server exited with Codex; the original Windows pane remained open. Temporary test authentication copies/symlinks were removed.
