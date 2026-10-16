# Development and verification

[README](../README.md) · [Command reference](REFERENCE.md) · [Agent instructions](../AGENTS.md)

## One native gate

From the repository with Python and fish 4+ on PATH:

```sh
python3 -W default tools/check.py
```

This compiles source without adding build artifacts, checks fish/shell syntax, then
runs stdlib unittest. Fish is required by the gate so completion tests cannot
silently skip. To focus on a failure, run `python3 -W default -m unittest discover
-s tests -v`; direct unittest can skip fish tests when fish is missing. No service,
pip install, network request, browser, or production dataset belongs in this gate.
`python3 -W default tools/check.py --quick` omits behavioral tests.

Interactive completion tests provide a pseudo-terminal and answer fish's Primary
Device Attribute query before waiting for the first prompt. A PTY alone does not
implement that terminal response; omitting it can stall newer fish versions.
The completion assertions still run; no terminal feature flag or timeout increase
is needed. See fish's [terminal compatibility contract](https://fishshell.com/docs/current/terminal-compatibility.html).

## Source map and decisions

| Location | Responsibility |
| --- | --- |
| `ww`: `main`, `run`, `project_root`, `read_marker` | CLI routing, argument-vector subprocesses, site discovery and marker validation. |
| `create`, `write_project`, `install_site` | Generate local WordPress/Lando setup, allocate collision-free app name and install. |
| `database_path`, `reset_db`, `destroy` | Validate local deletion targets, confirm and report recoverable partial states. |
| `_list_records`, `_container_root`, `list_projects`, `stop_all` | Group recorded containers, inspect Docker mounts only as fallback, execute site-scoped lifecycle actions. |
| `INSTANCE_INFO_PHP`, `instance_info`, `_StatusOutput` | One WP bootstrap and readable runtime/content overview. |
| Plugin lookup/pickers and `complete_wp` | Directory lookup, inline terminal choices and delegated completion. |
| `tricks/multisite/` | Trusted conversion script and manifest; see reference for extension contract. |
| `tests/`, `tools/check.py`, `completions/` | Native tests/checks and fish integration. |

One executable keeps this small wrapper easy to install/read. No database of ww
sites is maintained: Lando's recorded containers plus local markers are the source
of discovery, so a destroyed/not-yet-recorded site can be absent from list. Runtime
status describes recorded containers, not application health. One status eval
avoids repeated WordPress bootstraps; skipped ordinary plugins improve diagnosis
but do not make execution safe for untrusted sites.

The project stores source/database locally for inspectability; the absolute bind
mount makes moves deliberate. Destructive actions ask once, create no backup and
retain source. Adding a generic backup subsystem, Docker abstraction or automatic
resume would increase failure modes; explicit recovery is documented instead.
There is no lock against simultaneous edits/commands or hostile filesystem races.
Do not run lifecycle operations concurrently on the same site.

## Coverage and safe fixtures

`tests/test_ww.py` covers create, discovery, forwarded args/exits, lifecycle errors,
plugin pickers, status output and actual fish completion/PTY interaction.
`tests/test_tricks.py` covers catalog extension, confirmation/preflight and partial
multisite conversion/rewrite failure.

`tests/test_workflow.py` runs the real ww CLI in independent subprocesses against
stateful fake Lando/Docker executables. A temporary fixture bounds every call's
cwd. It verifies create → status from a child folder → WP-CLI plugin list → named
stop from another folder → stopped overview without WP calls → restart → confirmed
reset → confirmed destroy retaining source/config. It also verifies cancellation,
noninteractive refusal, failed-create retention/explicit recovery, failed-stop
preservation and a failed-start partial reset. A PTY supplies real confirmation
input; files and command logs prove which actions happened.

These are simulated service results. They do not establish Docker engine behavior,
real WordPress installation, TLS/routing, PHP/database compatibility or complete
terminal/accessibility coverage. Historical live checks are not new verification.
For live work, explicitly create a disposable site, record tool versions and the
journey/error outputs, and clean up only that site. Never use existing sites or
live `stop-all` as test fixtures.

## Making a change

Read the relevant function/tests, establish a failure with synthetic data, then
make the smallest change. Protect material failure/recovery behavior with a
regression, run the gate and `git diff --check`, inspect data effects and warnings,
and update user-facing help/reference if behavior changes. Report what changed,
what was actually checked, limitations and whether completion reinstall is needed.
Do not change the index/history or enable hooks without the user's instruction.

An optional `.githooks/pre-commit` checks staged whitespace and quick syntax only.
It does not install tools, modify/stage files or run services/tests. It is not
installed automatically. If you want it, first inspect `git config --get
core.hooksPath` and existing `.git/hooks/pre-commit`; preserve/compose existing
hooks. Only when no custom hook setup would be replaced, opt in yourself with:

```sh
git config --local core.hooksPath .githooks
```

Do full checks before proposing a change even when the quick hook passes.
Prepared GitHub Actions runs the same gate on Ubuntu 24.04/Python 3.14 with fish 4
from the fish maintainers' release PPA. It has read-only repository permission,
a short timeout, cancellation of superseded runs and no publish step. Workflow
configuration/local gate validation is not a remote CI pass; runner installation
and Linux behavior remain unobserved until executed. Actions/PPA dependencies
are outside a pip advisory audit. No dependency bot/package manager is justified
for a stdlib-only tool; updates are explicit maintenance.

## Security, warnings and maintenance

ww executes local Lando/Docker/WP-CLI and trusted trick code. Plugin directory
requests use HTTPS with a timeout, then Lando/WP-CLI downloads/executes plugin code.
Arguments use subprocess vectors rather than a shell. Local confirmation/path
checks reduce accidental loss; they are not a sandbox or permission boundary.
Database files, uploads, exports and wp-config may be sensitive. No telemetry is
implemented, but subprocesses/services can log and make network requests.

Use maintained Python/fish/Lando/Docker and review their own advisories. ww has no
third-party Python requirements/lockfile and no applicable pip dependency audit.
An offline check cannot certify the interpreter, operating system, containers,
WordPress or installed plugins as vulnerability-free. Inspect install/check/startup
output and keep advisory findings separate from deprecation notices.

The October 14 checks used macOS arm64 and fish 4.5.0. The initial Python 3.9.6
baseline is only a legacy compatibility observation: Python 3.9 is upstream
end-of-life. A disposable Python 3.14.8 ran the updated gate, clean-copy setup and
synthetic journey. Its initial HTTP-error resource warning prompted explicit
response closing and regressions; subsequent output had no reported resource or
deprecation warnings. There is no third-party Python audit result to claim.
Use a maintained interpreter on PATH; the disposable check does not update the
owner's system Python. Do not replace OS Python or suppress warnings. Runtime/tool
upgrades are owner-managed; there is no reason to migrate ww to a package framework.
Live Lando/Docker install/startup warnings remain unverified. See
[Python lifecycle](https://devguide.python.org/versions/).

See [CHANGELOG](../CHANGELOG.md) for unreleased behavior. Git commits identify
snapshots; no version/tag/release is invented. Project license and source provenance
are pending owner review. Before sharing,
review source, relevant history/metadata and licensing deliberately. Reporting a
suspected defect should include a redacted command/output, tool versions, data
impact and a minimal synthetic reproduction; never include secrets or private sites.
