"""Optional one-command launcher. Never installs software or changes shell profiles."""
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid

import terminals


def start(arguments):
    if os.name == 'nt':
        raise ValueError('On native Windows, run codex directly. The plugin opens supported panels automatically; '
                         'the task queue remains available without a panel. Use the launcher inside WSL for tmux.')
    arguments = arguments[1:] if arguments[:1] == ['--'] else arguments
    codex = shutil.which('codex')
    if not codex:
        raise ValueError('Codex CLI is not installed or is not on PATH.')
    command = [codex, *arguments]
    native = False
    try:
        terminal = terminals.detect()
        if terminal:
            owner = terminal.owner(terminal.panes())
            native = owner is not None and terminal.ready(owner)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        pass
    if not native and not any(os.environ.get(key) for key in ('TMUX', 'STY', 'ZELLIJ')):
        tmux = shutil.which('tmux')
        if tmux and sys.stdin.isatty() and sys.stdout.isatty():
            # A private server ignores user config and cannot resize or close unrelated sessions.
            with tempfile.TemporaryDirectory(prefix='codex-tasklist-start-') as directory:
                marker = Path(directory) / 'started'
                child = [sys.executable, str(Path(__file__).resolve()), str(marker), *command]
                tmux_command = [tmux, '-L', 'codex-tasklist-' + uuid.uuid4().hex, '-f', os.devnull,
                                'new-session', '-s', 'codex', 'exec ' + shlex.join(child),
                                ';', 'set-option', '-t', 'codex', 'status', 'off',
                                ';', 'set-option', '-t', 'codex', 'mouse', 'on',
                                ';', 'set-option', '-t', 'codex', 'destroy-unattached', 'on']
                try:
                    result = subprocess.call(tmux_command)
                except OSError:
                    result = 1
                if result and not marker.exists():
                    # Stop a partly created server before checking the marker again.
                    try:
                        subprocess.run([*tmux_command[:3], 'kill-server'], capture_output=True, timeout=3)
                    except (OSError, subprocess.SubprocessError):
                        return result
                # Never start a second Codex if the first one already ran.
                if result == 0 or marker.exists():
                    try:
                        return int(marker.with_name('exit-code').read_text())
                    except (OSError, ValueError):
                        return result or 1
            print('Codex Tasklist: panel setup failed; starting Codex without a panel.', file=sys.stderr)
        else:
            print('Codex Tasklist: continuing without a panel; your task queue remains available. '
                  'For a panel, install tmux on Linux/macOS/WSL or use a supported direct integration.',
                  file=sys.stderr)
    os.execv(command[0], command)


def install(script, directory, bin_dir=None):
    if os.name == 'nt':
        raise ValueError('On native Windows, run codex directly; no launcher installation is needed. '
                         'For automatic tmux setup, install the launcher inside WSL.')
    if bin_dir is None:
        bin_dir = Path.home() / '.local/bin'
    bin_dir = Path(bin_dir).expanduser().resolve()
    marker = 'Codex Tasklist launcher'
    command = [sys.executable, str(script), '--data-dir', str(directory), 'start', '--']
    path = bin_dir / 'codex-tasklist'
    content = f'#!/bin/sh\n# {marker}\nexec {shlex.join(command)} "$@"\n'
    if path.is_symlink() or (path.exists() and (not path.is_file() or not any(
            line == '# ' + marker for line in path.read_text().splitlines()[:2]))):
        raise ValueError(f'Refusing to replace an unrelated file: {path}')
    bin_dir.mkdir(parents=True, exist_ok=True)
    temporary = bin_dir / ('.codex-tasklist-' + uuid.uuid4().hex)
    try:
        with temporary.open('x') as output:
            output.write(content)
        temporary.chmod(0o755)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


if __name__ == '__main__':
    # Keep a tiny supervisor so tmux's successful detach cannot hide a Codex failure.
    marker = Path(sys.argv[1])
    marker.touch()
    # The child shares our terminal process group and handles Ctrl-C itself.
    signal.signal(signal.SIGINT, lambda *_: None)
    try:
        result = subprocess.call(sys.argv[2:])
    except OSError:
        result = 127
    result = result if result >= 0 else 128 - result
    marker.with_name('exit-code').write_text(str(result))
    sys.exit(result)
