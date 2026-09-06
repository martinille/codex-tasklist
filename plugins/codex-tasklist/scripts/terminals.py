"""Small terminal adapters; all commands target an identified pane, never focus."""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import process_owner


def run(arguments):
    return subprocess.run(list(map(str, arguments)), check=True, capture_output=True,
                          text=True, encoding='utf-8', timeout=3).stdout.strip()


def current_pane(kind):
    if kind == 'ghostty':
        return ''  # Ghostty renderers are identified by the launch token, not an inherited ID.
    variable = {'wezterm': 'WEZTERM_PANE', 'tmux': 'TMUX_PANE',
                'kitty': 'KITTY_WINDOW_ID', 'iterm2': 'ITERM_SESSION_ID'}[kind]
    value = os.environ.get(variable, '')
    return value.rsplit(':', 1)[-1] if kind == 'iterm2' else value


def detect():
    # Inner multiplexers own the PTY. Never fall through to inherited outer IDs.
    if os.environ.get('TMUX'):
        if shutil.which('tmux') and re.fullmatch(r'%[0-9]+', current_pane('tmux')):
            return Terminal('tmux', current_pane('tmux'), os.environ['TMUX'].rsplit(',', 2)[0])
        return None
    if any(os.environ.get(key) for key in ('STY', 'ZELLIJ', 'SSH_CONNECTION', 'SSH_TTY')):
        return None
    program = os.environ.get('TERM_PROGRAM', '')
    # kitty sets TERM, but can inherit TERM_PROGRAM and outer terminal pane IDs.
    if ((program in ('', 'kitty') or os.environ.get('TERM') == 'xterm-kitty') and current_pane('kitty').isdigit() and
            shutil.which('kitten') and os.environ.get('KITTY_LISTEN_ON', '').startswith('unix:')):
        return Terminal('kitty', current_pane('kitty'), os.environ['KITTY_LISTEN_ON'])
    if program in ('', 'WezTerm') and current_pane('wezterm').isdigit() and shutil.which('wezterm'):
        return Terminal('wezterm', current_pane('wezterm'), os.environ.get('WEZTERM_UNIX_SOCKET', ''))
    if (sys.platform == 'darwin' and program == 'iTerm.app' and
            re.fullmatch(r'[A-Za-z0-9-]+', current_pane('iterm2')) and shutil.which('osascript')):
        return Terminal('iterm2', current_pane('iterm2'), '')
    if sys.platform == 'darwin' and program == 'ghostty' and shutil.which('osascript'):
        device = run(['ps', '-p', os.getpid(), '-o', 'tty='])
        if re.fullmatch(r'ttys[0-9]+', device):
            return Terminal('ghostty', '/dev/' + device, '')
    return None


class Terminal:
    def __init__(self, kind, parent, socket):
        self.kind, self.parent, self.socket = kind, parent, socket

    def valid_id(self, pane):
        pattern = r'%[0-9]+' if self.kind == 'tmux' else (
            r'[0-9]+' if self.kind in ('wezterm', 'kitty') else r'[A-Za-z0-9-]+')
        return re.fullmatch(pattern, pane) is not None

    def command(self, *arguments):
        if self.kind == 'wezterm':
            return run(['wezterm', 'cli', *arguments])
        if self.kind == 'tmux':
            return run(['tmux', '-S', self.socket, *arguments])
        if self.kind == 'kitty':
            return run(['kitten', '@', '--to', self.socket, *arguments])
        return run(['osascript', '-e', arguments[0], *arguments[1:]])

    def panes(self):
        if self.kind == 'wezterm':
            return json.loads(self.command('list', '--format', 'json'))
        if self.kind == 'tmux':
            output = self.command('list-panes', '-a', '-F', '#{pane_id}\t#{window_id}\t#{pane_height}')
            return [{'pane_id': pane, 'tab_id': tab, 'size': {'rows': int(rows)}}
                    for pane, tab, rows in (line.split('\t') for line in output.splitlines())]
        if self.kind == 'kitty':
            return [{'pane_id': pane['id'], 'tab_id': tab['id'], 'size': {'rows': pane['lines']},
                     'layout': tab['layout'], 'pid': pane.get('pid')}
                    for window in json.loads(self.command('ls')) for tab in window['tabs']
                    for pane in tab['windows']]
        if self.kind == 'ghostty':
            output = self.command('''tell application "Ghostty"
set resultText to ""
repeat with w in windows
repeat with t in tabs of w
repeat with s in terminals of t
set resultText to resultText & (id of s) & (ASCII character 9) & (id of t) & (ASCII character 9) & (tty of s) & linefeed
end repeat
end repeat
end repeat
return resultText
end tell''')
            result = []
            for pane, tab, device in (line.split('\t') for line in output.splitlines()):
                rows = 0
                if re.fullmatch(r'/dev/ttys[0-9]+', device):
                    try:
                        fd = os.open(device, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
                        try:
                            rows = os.get_terminal_size(fd).lines
                        finally:
                            os.close(fd)
                    except OSError:
                        pass
                result.append({'pane_id': pane, 'tab_id': tab, 'tty': device, 'size': {'rows': rows}})
            return result
        output = self.command('''tell application "iTerm2"
set resultText to ""
repeat with w in windows
repeat with t in tabs of w
repeat with s in sessions of t
set resultText to resultText & (unique id of s) & (ASCII character 9) & (id of w as text) & ":" & (index of t as text) & (ASCII character 9) & (rows of s as text) & linefeed
end repeat
end repeat
end repeat
return resultText
end tell''')
        return [{'pane_id': pane, 'tab_id': tab, 'size': {'rows': int(rows)}}
                for pane, tab, rows in (line.split('\t') for line in output.splitlines())]

    def ready(self, owner):
        return self.kind != 'kitty' or owner.get('layout') == 'splits'

    def owner(self, panes):
        key = 'tty' if self.kind == 'ghostty' else 'pane_id'
        pane = next((pane for pane in panes if str(pane.get(key, '')) == self.parent), None)
        return pane if pane is not None and self.owns(pane) else None

    def owns(self, pane):
        if self.kind not in ('wezterm', 'kitty'):
            return True
        if os.name == 'nt':
            return self.kind == 'wezterm' and process_owner.in_windows_wezterm()
        current = process_owner.terminal_tty(os.getpid())
        if not current:
            return False
        if self.kind == 'kitty':
            return bool(pane.get('pid')) and process_owner.terminal_tty(pane['pid']) == current
        device = pane.get('tty_name')
        if not isinstance(device, str) or not device.startswith('/dev/'):
            return False
        try:
            candidate = str(os.stat(device).st_rdev) if sys.platform.startswith('linux') else device
            return candidate == current
        except OSError:
            return False

    def split(self, owner, height, arguments):
        if self.kind == 'wezterm':
            pane = self.command('split-pane', '--pane-id', self.parent, '--bottom', '--cells', height,
                                '--', *arguments)
            # A focus failure must not hide the ID of a successfully created pane.
            try:
                self.command('activate-pane', '--pane-id', self.parent)
            except (OSError, subprocess.SubprocessError):
                pass
            return pane
        if self.kind == 'tmux':
            return self.command('split-window', '-t', self.parent, '-v', '-d', '-l', height,
                                '-P', '-F', '#{pane_id}', 'exec ' + shlex.join(list(map(str, arguments))))
        if self.kind == 'kitty':
            return self.command('launch', '--match', f"id:{owner['tab_id']}", '--next-to', f'id:{self.parent}',
                                '--location', 'hsplit', '--bias', 100 * height / owner['size']['rows'],
                                '--dont-take-focus', '--', *arguments)
        if self.kind == 'ghostty':
            return self.ghostty('''set previousFocus to missing value
try
if frontmost then set previousFocus to focused terminal of selected tab of front window
end try
set cfg to new surface configuration
set command of cfg to item 2 of argv
set wait after command of cfg to false
set child to split targetPane direction down with configuration cfg
try
if previousFocus is not missing value and frontmost then focus previousFocus
end try
return id of child''', owner['pane_id'], shlex.join(list(map(str, arguments))))
        return self.iterm('''set child to split horizontally with default profile command (item 2 of argv)
try
set rows of child to (item 3 of argv as integer)
end try
return unique id of child''', self.parent, shlex.join(list(map(str, arguments))), height)

    def iterm(self, action, pane, *arguments):
        # Values go through argv, not through AppleScript interpolation.
        return self.command('''on run argv
tell application "iTerm2"
repeat with w in windows
repeat with t in tabs of w
repeat with s in sessions of t
if unique id of s is item 1 of argv then
tell s
''' + action + '''
end tell
end if
end repeat
end repeat
end repeat
error "Tasklist terminal session is unavailable"
end tell
end run''', pane, *arguments)

    def ghostty(self, action, pane, *arguments):
        return self.command('''on run argv
tell application "Ghostty"
set targetPane to first terminal whose id is item 1 of argv
''' + action + '''
end tell
end run''', pane, *arguments)

    def close(self, pane):
        if self.kind == 'wezterm':
            self.command('kill-pane', '--pane-id', pane)
        elif self.kind == 'tmux':
            self.command('kill-pane', '-t', pane)
        elif self.kind == 'kitty':
            self.command('close-window', '--match', f'id:{pane}')
        elif self.kind == 'ghostty':
            self.ghostty('close targetPane\nreturn ""', pane)
        else:
            self.iterm('close\nreturn ""', pane)

    def resize(self, pane, height, difference):
        if self.kind == 'wezterm':
            self.command('adjust-pane-size', '--pane-id', pane, '--amount', abs(difference),
                         'Up' if difference > 0 else 'Down')
        elif self.kind == 'tmux':
            self.command('resize-pane', '-t', pane, '-y', height)
        elif self.kind == 'kitty':
            # Kitty converts cells to a fractional split bias; rounding can miss a row.
            for _ in range(3):
                self.command('resize-window', '--match', f'id:{pane}', '--axis', 'vertical', '--increment', difference)
                current = next((item['size']['rows'] for item in self.panes()
                                if str(item['pane_id']) == pane), None)
                if current is None or current == height:
                    break
                difference = height - current
        elif self.kind == 'ghostty':
            action = f"resize_split:{'up' if difference > 0 else 'down'},{abs(difference)}"
            self.ghostty('perform action (item 2 of argv) on targetPane\nreturn ""', pane, action)
        else:
            self.iterm('set rows to (item 2 of argv as integer)\nreturn ""', pane, height)
