# Project instructions

- For every change, tell the user in the final response whether they need to rerun
  `completions/fish-install.sh`. Completion or PATH changes require
  `./completions/fish-install.sh` from the repository root. Executable-only changes
  take effect in place. Do not install into the user's configuration speculatively.
- Read [README](README.md) and [development](docs/DEVELOPMENT.md); use the
  [reference](docs/REFERENCE.md) for detailed command behavior. Recheck Git state
  and preserve existing edits. Commit/push/publication require explicit instructions.
- Keep Python stdlib and the single executable. Refactor only for a concrete safety
  or correctness need; no package manager, framework, release machinery or new
  dependency solely for audits or bots.
- Run `python3 -W default tools/check.py` after behavior changes. It needs fish 4+
  and uses synthetic fixtures; Lando/Docker are unnecessary. `--quick` only checks
  syntax and is not complete behavior verification. Review warnings, not just exit
  status. Use a maintained Python; legacy-interpreter results are compatibility
  evidence, not assurance about that runtime's security support.
- Use fake Lando or an explicitly disposable synthetic site. Never start, stop,
  reset, destroy or convert the user's existing sites as a check. Never use live
  global `stop-all` for smoke testing. Even `lando list` can write global config;
  respect filesystem/network approvals. Tricks execute trusted local code and may
  modify data; inspect them before running.
- A site has `.ww.json` (schema 1) and `.lando.yml`; source is `wordpress/` and MySQL
  data is bind-mounted from `db/`. Resolve commands from roots or child directories.
  Database deletion rejects symlinks/non-directories before Lando mutation and
  rechecks immediately before removal. There is no concurrent-operation lock or
  automatic backup/rollback; do not promise either.
- Create refuses existing/nested environments and conflicting source/data/config
  targets. Failures retain files; starting them does not complete installation.
  `reset-db` confirms, stops, deletes data, starts and reinstalls. `destroy` confirms,
  destroys containers and deletes data while keeping source/config. Preserve
  recoverable partial states and useful stderr/nonzero exits.
- `list_projects()` filters Lando records by marker/app identity; `stop-all()`
  attempts running/partial sites, reports per-site errors and returns 1 on aggregate
  failure. WP-CLI/tricks pass through their exit status; usage is 2, wrapper errors
  are 1, top-level interruptions are 130. Explicit picker cancellation retains its
  existing behavior.
- `status`/`info` are aliases and never start services. One `INSTANCE_INFO_PHP`
  eval retrieves WordPress details only for fully running recorded containers,
  skipping normal plugins/themes (must-use plugins still load). DB size is table
  data/index metadata across the database, not the disk usage of `db/`.
- Preserve the structured status layout, redirected plain output, `NO_COLOR`,
  `TERM=dumb`, narrow/wide wrapping and Unicode terminal widths. Plugin menus remain
  inline with keyboard interaction and restore terminal settings on failure.
- Completion is copied on install while the executable runs from this repository.
  `ww wp` delegates suggestions to the site's WP-CLI and stays quiet on failure.
  Tricks are `tricks/<slug>/{manifest.json,run.py}` and receive the absolute site
  root as their argument/cwd; additions are discovered dynamically.
- Multisite is Apache/subdirectory-only, confirms without backup, rejects custom
  or overriding rewrite files, writes `.htaccess` atomically and verifies the
  network. Partial conversion errors must say what may already have changed.
- Keep docs generic and use synthetic examples. Do not put personal paths, private
  site data, credentials beyond documented local defaults, or unrelated context in
  fixtures/docs. Keep claims about users, metrics, attribution, license rights,
  release history and live verification accurate.
