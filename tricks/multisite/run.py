#!/usr/bin/env python3
"""Convert a ww site to a path-based WordPress multisite network."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


BEGIN = "# BEGIN WordPress"
END = "# END WordPress"
REWRITE_BLOCK = """# BEGIN WordPress
RewriteEngine On
RewriteBase /
RewriteRule ^index\\.php$ - [L]

RewriteRule ^([_0-9a-zA-Z-]+/)?wp-admin$ $1wp-admin/ [R=301,L]

RewriteCond %{REQUEST_FILENAME} -f [OR]
RewriteCond %{REQUEST_FILENAME} -d
RewriteRule ^ - [L]
RewriteRule ^([_0-9a-zA-Z-]+/)?(wp-(content|admin|includes).*) $2 [L]
RewriteRule ^([_0-9a-zA-Z-]+/)?(.*\\.php)$ $2 [L]
RewriteRule . index.php [L]
# END WordPress"""


class TrickError(Exception):
    """A conversion error suitable for terminal output."""


def lando(root: Path, *args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["lando", *args], cwd=root, text=True, check=False,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except OSError as exc:
        raise TrickError(f"Could not run Lando: {exc}") from exc


def appserver_from_info(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        direct = value.get("appserver")
        if isinstance(direct, dict):
            return direct
        if value.get("service") == "appserver":
            return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("service", item.get("name")) == "appserver":
                return item
    return None


def rewrite_contents(existing: str) -> str:
    if not existing.strip():
        return REWRITE_BLOCK + "\n"
    if existing.count(BEGIN) != 1 or existing.count(END) != 1:
        raise TrickError("Existing .htaccess has no single WordPress rewrite block; review it before converting.")
    start = existing.index(BEGIN)
    end = existing.index(END) + len(END)
    if start >= end or existing.index(END) < start:
        raise TrickError("Existing .htaccess has malformed WordPress rewrite markers.")
    return existing[:start] + REWRITE_BLOCK + existing[end:]


def preflight(root: Path) -> tuple[str, Path, str]:
    if not (root / ".ww.json").is_file() or not (root / ".lando.yml").is_file():
        raise TrickError("Run this trick from a ww project.")
    webroot = root / "wordpress"
    config = webroot / "wp-config.php"
    if config.is_symlink() or not config.is_file() or not os.access(config, os.W_OK):
        raise TrickError(f"WordPress config is missing or not writable: {config}")
    if not os.access(webroot, os.W_OK):
        raise TrickError(f"WordPress folder is not writable: {webroot}")
    lando_rewrite = webroot / ".htaccess-lando"
    if os.path.lexists(lando_rewrite):
        raise TrickError(".htaccess-lando overrides .htaccess in Lando; remove or adapt it before converting.")
    rewrite = webroot / ".htaccess"
    if rewrite.is_symlink() or (rewrite.exists() and (not rewrite.is_file() or not os.access(rewrite, os.W_OK))):
        raise TrickError(f"Rewrite file is not a writable regular file: {rewrite}")
    try:
        prepared_rewrite = rewrite_contents(rewrite.read_text(encoding="utf-8") if rewrite.exists() else "")
    except (OSError, UnicodeError) as exc:
        raise TrickError(f"Cannot read {rewrite}: {exc}") from exc

    info_result = lando(root, "info", "--format=json", capture=True)
    if info_result.returncode != 0:
        raise TrickError("Could not inspect Lando appserver. Start this site with `ww start` first.")
    try:
        info = json.loads(info_result.stdout)
    except ValueError as exc:
        raise TrickError("Lando returned invalid appserver information.") from exc
    appserver = appserver_from_info(info)
    via = appserver.get("via") if appserver else None
    if not isinstance(via, str) or not via.startswith("apache"):
        raise TrickError("This trick requires a Lando Apache appserver; nginx needs different rewrite rules.")

    installed = lando(root, "wp", "core", "is-installed", capture=True)
    if installed.returncode != 0:
        raise TrickError("WordPress is not installed or cannot be reached. Start the site with `ww start`.")
    if lando(root, "wp", "core", "is-installed", "--network", capture=True).returncode == 0:
        raise TrickError("This site is already a multisite network.")
    home_result = lando(root, "wp", "option", "get", "home", capture=True)
    if home_result.returncode != 0:
        raise TrickError("Could not read the WordPress home URL.")
    home = home_result.stdout.strip()
    parsed = urlsplit(home)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise TrickError(f"This trick requires a root-path WordPress home URL; found {home!r}.")
    return home.rstrip("/"), rewrite, prepared_rewrite


def write_rewrite(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".htaccess.ww-", delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def convert(root: Path) -> int:
    home, rewrite, prepared_rewrite = preflight(root)
    if not sys.stdin.isatty():
        raise TrickError("Multisite conversion needs an interactive terminal for confirmation.")
    print(f"Convert {root} to a subdirectory-based multisite network ({home})?")
    print("No backup will be created. Existing sites and their URLs may need review after conversion.")
    try:
        answer = input("Type yes to continue: ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != "yes":
        print("Cancelled.")
        return 0

    result = lando(root, "wp", "core", "multisite-convert", "--base=/")
    if result.returncode != 0:
        raise TrickError("WP-CLI conversion failed. The database or wp-config.php may have changed; inspect them before retrying.")
    try:
        write_rewrite(rewrite, prepared_rewrite)
    except OSError as exc:
        raise TrickError(f"WordPress conversion completed, but .htaccess could not be updated: {exc}. Add multisite rewrite rules manually.") from exc
    if lando(root, "wp", "core", "is-installed", "--network", capture=True).returncode != 0:
        raise TrickError("Conversion and rewrite update completed, but the network check failed; inspect the site before retrying.")
    print(f"Multisite ready. Network admin: {home}/wp-admin/network/")
    return 0


def main(args: list[str]) -> int:
    if len(args) != 1:
        print("multisite: expected the ww project folder.", file=sys.stderr)
        return 2
    try:
        return convert(Path(args[0]).resolve())
    except TrickError as exc:
        print(f"multisite: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
