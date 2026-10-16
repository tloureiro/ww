# Command reference

[Setup and first task](../README.md) · [Development and checks](DEVELOPMENT.md)

From a project folder that does not already contain a Lando site:

```fish
ww create
ww status
ww info
ww wp plugin list
ww plugin install akismet
ww plugin install cache 1.2.3
ww plugin list
ww tricks
ww tricks multisite
ww stop
ww start
ww list
```

`ww create` adds `.lando.yml`, `.ww.json`, `wp-cli.yml`, `wordpress/`, and `db/` to the current folder. It starts and installs WordPress. The site URL is printed at the end. The local admin login is `admin` / `admin`, email `a@a.com`. Change these credentials if you expose the site beyond local development.

## Lifecycle and named sites

`ww stop [site]` and `ww destroy [site]` accept a site name from `ww list`, so they can run from another folder. Fish completion after either command lists sites with running containers. Without a name, each command uses the current site.

`ww stop-all` runs from any directory and stops every ww site with running containers, including partially running sites. It skips stopped sites. If a site fails to stop, it still attempts the others, reports the failures, and returns a nonzero exit status.

## Status and presentation

`ww status` and `ww info` are synonyms. Both show the instance's runtime state and project folder, including when run from a child directory. A fully running instance also shows the site title and URLs, WordPress and PHP versions, database name and server version, active theme (and parent theme), user count, published post/page counts, multisite status and site count, debug mode, and installed plugins with versions and activation states. Active counts include network-active plugins; must-use plugins and drop-ins are listed separately in the totals. In multisite, theme, users, content, and regular plugin activation describe the default site selected by WP-CLI. Database size is the approximate total of table data and indexes for the entire configured database, including all multisite tables; it is not the disk usage of `db/`. Unavailable database size does not hide the rest of the overview. Stopped, partially running, or destroyed instances show only runtime information and are never started by these commands. If WordPress cannot be read on a running instance, runtime information is still shown and the command reports the error.

The overview uses aligned Site, WordPress, Database, and Plugins sections, with a plugin table and colored runtime/activation states in terminals. It adapts to narrow terminals and omits ANSI color when redirected, when `NO_COLOR` is set, or with `TERM=dumb`.

The Lando environment takes the current folder's name. If that name is already used by a running or stopped Lando app, `ww` adds `-1`, `-2`, and so on.

WordPress source and database files live in the project. The `db/` folder is added to `.gitignore`. Lando and Docker still store images, containers, logs, and global configuration elsewhere. The generated `.lando.yml` uses the project's absolute path for the database bind mount; update that path if you move the project.

## Plugin installation

`ww plugin install <slug-or-search> [version]` checks for an exact WordPress.org slug. In an interactive terminal, an exact slug opens the picker with one plugin, so you can press Enter for the current release or `v` to choose a published version. A version supplied as a command argument installs directly. Partial searches show up to ten matches, sorted by number of ratings. The inline picker keeps a compact comparison list and shows full details for the highlighted plugin. Use Up/Down to move, Enter to install and activate, or `v` to browse that plugin's published versions. In the version view, Up/Down and Enter install and activate the selected version; Esc returns to the plugin list and q cancels. The details include rating, rating count, downloads, active installs, version, and last update. A version chosen in the picker takes precedence over a command argument for partial searches. Noninteractive exact slug installation still works directly; a partial search needs an interactive terminal. Use `ww wp ...` for all other WP-CLI operations and their normal options.

## Help and fish completion

`ww plugin install --help` (or `-h`) shows syntax and picker controls from any directory, without requiring a site.

In a running site, type `ww wp ` (including the space) and press Tab twice to browse WP-CLI commands in fish's native completion panel. Use the arrow keys and Enter to select a command, then press Tab again for its subcommands or options, for example after `ww wp plugin ` or `ww wp plugin list --`. Suggestions come from the site's WP-CLI through Lando and also work from child directories. If Lando or WP-CLI is unavailable, completion stays quiet.

In fish, press Tab twice after `ww plugin install` to open fish's native completion panel with `slug-or-search`, `slug-or-search version`, and `--help`. The argument templates include examples in their descriptions. Fish escapes the space in the version template while you navigate the panel; press Enter to accept it and the completion helper converts it into two separate arguments, ready for you to replace with your own values. The helper preserves existing Enter bindings and applies only to this template. Typing `--h` and pressing Tab completes the actual `--help` option.

If the plugin is already installed, `ww plugin install` activates it. Supplying a version for an installed plugin reinstalls that version before activation.

## Installed-plugin actions

`ww plugin list` opens an inline list of installed plugins with their status and version. Use Up/Down to move, `a` to activate, `d` to deactivate, and `r` to remove the selected plugin. Removal asks for Enter confirmation and deletes plugin files; it deactivates an active plugin first. Plugin settings and content may remain in the database. Esc or q exits, and the list refreshes after each change. WordPress must be running, and the list needs an interactive terminal. Network active, must use, and drop in plugins are shown but cannot be changed in this picker.

## Tricks

`ww tricks` opens a compact inline menu of reusable tasks. It shows each trick's name, slug, and description. Use Up/Down and Enter to run one, or Esc/q to exit. The menu closes before the trick runs, so its output stays visible in the terminal. Run `ww tricks <slug>` to skip the menu; this also works from scripts when the trick does not require its own interactive input. Trick exit codes are passed through.

The first trick is `ww tricks multisite`. It converts an installed site to a subdirectory-based multisite network using WP-CLI, then installs Apache rewrite rules in `wordpress/.htaccess`. It needs a running Lando Apache appserver and a root-path WordPress URL. It refuses an existing network, `.htaccess-lando`, and an unrecognized `.htaccess`. It asks for `yes` before conversion and does **not** create a backup. The main site's URL remains the same; new sites use paths such as `/team/`.

To add a trick, create `tricks/<slug>/manifest.json` with nonempty `name` and `description` strings and a `run.py` in the same folder. The slug uses lowercase letters, digits, and internal hyphens. `ww` invokes `run.py` with Python, passing the absolute project folder as its only argument and using that folder as the working directory. The script can use other files in its folder and may call Lando, WP-CLI, or edit project files. Write progress to stdout, errors to stderr, and return a meaningful exit code. New folders appear automatically in the menu and slug completion.

## Reset and destroy

`ww reset-db` stops the site, deletes `db/`, starts it again, and reinstalls WordPress with the default admin login. `ww destroy` destroys the Lando environment and deletes `db/`, keeping the source and configuration files. Both commands require an interactive confirmation. Destroyed sites do not appear in `ww list`; a later `ww start` starts the retained configuration with an uninstalled database.


## Recovering an incomplete install

First inspect the retained `.ww.json`, `.lando.yml`, `wp-cli.yml`, `wordpress/` and
`db/`. Configuration-file creation can also fail; repair missing files deliberately
before attempting `ww start`. Creating again refuses the existing environment.
There is no automatic resume or rollback.

After resolving the original service error and starting the retained site, check
`ww wp core is-installed`. Only for a new, unfinished site: download source if it
is absent, create config if it is absent, then install if WordPress is uninstalled.
Do not overwrite an existing database or config. Read the generated marker for the
actual name (it may have a collision suffix) and title. For a site named `ww-demo`:

```fish
ww wp core download
ww wp config create --dbname=wordpress --dbuser=wordpress --dbpass=wordpress --dbhost=database
ww wp core install --url=https://ww-demo.lndo.site --title='ww demo' --admin_user=admin --admin_password=admin --admin_email=a@a.com --skip-email
ww status
```

These are conditional recovery steps, not a script to paste over an existing site.
A fresh install after `destroy` needs database installation again; `ww start`
does not perform it. A failed reset has no automatic way to restore old data.
Multisite conversion can partially change the database and `wp-config.php` before
rewrite/network verification fails; inspect the reported phase and restore your
own backup instead of blindly converting again.
