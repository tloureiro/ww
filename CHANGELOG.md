# Changes

## Unreleased

- Answer fish's terminal capability handshake in the interactive completion test
  fixture so newer fish versions can reach the prompt in offline checks.

- Reject symlinked or non-directory `db/` before reset/destroy invokes Lando.
- Handle malformed Docker inspection and unknown Lando list responses safely.
- Preserve partially created files and explain failures during creation, database
  reset and post-destroy cleanup. Interruptions return 130 without a traceback.
- Accept `--help` and `-h`; show destructive data effects in general help.
- Support quoted/escaped executable paths in WP-CLI fish completion. Rerun
  `./completions/fish-install.sh` to replace the installed completion, then open a
  new fish shell.
- Add a stateful fake-Lando lifecycle/recovery test, an offline check command,
  optional quick hook and prepared GitHub Actions checks.
- Organize setup, detailed command reference, recovery and contributor guidance.
- Remove the untracked `ww/1.0` HTTP User-Agent version claim.
- Close HTTP error responses explicitly; keep warning-enabled checks clean on a
  maintained interpreter.

The project has no release tags or declared release version. Identify snapshots by
Git commit; the entries above describe unreleased work. No version bump, release,
packaging, or automatic publication is needed for this local tool. Review behavior,
data effects, license and attribution before distributing a snapshot. Completion
changes require rerunning the installer; executable changes take effect in place.
