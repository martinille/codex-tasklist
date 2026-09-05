# Security

## Reporting a vulnerability

Please email **ille.martin@gmail.com** with the affected version, reproduction steps, and impact. Do not include credentials or private task data. Avoid publishing exploit details in a public issue before a fix is available.

Security fixes target the latest release. Update to the latest version before reporting a problem.

## Scope

Codex Tasklist runs locally with the permissions of its Codex session. It stores task titles and statuses in SQLite and invokes the local WezTerm CLI to manage its panel. It does not upload task data. Review hooks before trusting them and keep secrets out of task titles.

The plugin uses only the Python standard library; there are no third-party runtime dependencies to lock.
