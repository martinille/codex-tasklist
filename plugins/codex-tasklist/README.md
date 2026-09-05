# Codex Tasklist

A persistent per-session task queue for Codex CLI, with a live scrolling panel in WezTerm. Requires Python 3.10+ and uses only the standard library.

Write requests normally: Codex creates tasks, matches follow-ups, and updates statuses. Each session keeps its own saved queue. The panel opens with a live Codex CLI session and closes when its owner exits. Outside WezTerm, queue commands remain available without the panel.

See the [installation guide, demo and controls](https://github.com/martinille/codex-tasklist#readme). Review and trust hooks before use. See [SECURITY.md](SECURITY.md) for vulnerability reporting and [LICENSE](LICENSE) for the MIT license.
