import importlib.machinery
import importlib.util
import fcntl
import io
import json
import os
import pty
import re
import select
import shlex
import shutil
import struct
import subprocess
import tempfile
import termios
import time
import unittest
import urllib.error
from contextlib import ExitStack, contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "ww"
loader = importlib.machinery.SourceFileLoader("ww_cli", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
ww = importlib.util.module_from_spec(spec)
loader.exec_module(ww)


class FakeTTYOutput(io.StringIO):
    def isatty(self):
        return True


@contextmanager
def fake_picker(keys, terminal=(120, 24)):
    output = FakeTTYOutput()
    with ExitStack() as stack:
        stack.enter_context(patch.object(ww.sys.stdin, "isatty", return_value=True))
        stack.enter_context(patch.object(ww.sys.stdin, "fileno", return_value=0))
        stack.enter_context(patch.object(ww.sys, "stdout", output))
        stack.enter_context(patch.object(ww.termios, "tcgetattr", return_value=[]))
        stack.enter_context(patch.object(ww.termios, "tcsetattr"))
        stack.enter_context(patch.object(ww.tty, "setcbreak"))
        stack.enter_context(patch.object(ww, "_plugin_key", side_effect=keys))
        stack.enter_context(patch.object(ww.shutil, "get_terminal_size", return_value=terminal))
        yield output


class CreateTests(unittest.TestCase):
    def test_configuration_write_failure_retains_files_without_starting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".gitignore").mkdir()
            with patch.object(ww, "require_lando"), patch.object(ww, "_list_records", return_value=[]), patch.object(ww, "run") as run:
                with self.assertRaisesRegex(ww.WWError, "Creation stopped.*files were kept"):
                    ww.create(root)
                run.assert_not_called()
            self.assertTrue((root / "wordpress").is_dir())
            self.assertTrue((root / ".ww.json").is_file())

    def test_create_writes_project_and_installs_site(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my-site"
            root.mkdir()
            with patch.object(ww, "require_lando"), patch.object(ww, "_list_records", return_value=[]), patch.object(ww, "run") as run:
                with redirect_stdout(io.StringIO()):
                    ww.create(root)
            self.assertTrue((root / "wordpress").is_dir())
            self.assertTrue((root / "db").is_dir())
            self.assertEqual((root / "wp-cli.yml").read_text(), "path: wordpress\n")
            self.assertIn("php: '8.3'", (root / ".lando.yml").read_text())
            self.assertIn("database: mysql:8.0", (root / ".lando.yml").read_text())
            self.assertIn(str(root / "db"), (root / ".lando.yml").read_text())
            self.assertIn("db/", (root / ".gitignore").read_text())
            marker = json.loads((root / ".ww.json").read_text())
            self.assertEqual(marker["name"], "my-site")
            self.assertEqual(marker["title"], "my site")
            self.assertIn('name: "my-site"', (root / ".lando.yml").read_text())
            calls = [call.args[0] for call in run.call_args_list]
            self.assertEqual(calls[0], ["lando", "start"])
            self.assertIn(["lando", "wp", "core", "download"], calls)
            self.assertTrue(any(call[:4] == ["lando", "wp", "core", "install"] for call in calls))

    def test_create_uses_next_free_name_including_stopped_apps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "my-site"
            root.mkdir()
            records = [
                {"app": "mysite", "running": False},
                {"app": "mysite1", "running": False},
                {"app": "mysite2", "running": True},
            ]
            with patch.object(ww, "require_lando"), patch.object(ww, "_list_records", return_value=records), patch.object(ww, "run"), redirect_stdout(io.StringIO()):
                ww.create(root)
            marker = json.loads((root / ".ww.json").read_text())
            self.assertEqual(marker["name"], "my-site-3")
            self.assertIn('name: "my-site-3"', (root / ".lando.yml").read_text())

    def test_create_refuses_existing_environment_and_target_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "site"
            root.mkdir()
            (root / ".lando.yml").write_text("name: old\n")
            with self.assertRaisesRegex(ww.WWError, "already exists"):
                ww.create(root)
            (root / ".lando.yml").unlink()
            (root / "wordpress").mkdir()
            with self.assertRaisesRegex(ww.WWError, "Refusing to overwrite"):
                ww.create(root)

    def test_create_refuses_nested_lando_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".lando.yml").write_text("name: parent\n")
            child = root / "child"
            child.mkdir()
            with self.assertRaisesRegex(ww.WWError, "already exists"):
                ww.create(child)


class PluginTests(unittest.TestCase):
    def test_install_help_works_outside_a_site(self):
        with tempfile.TemporaryDirectory() as directory:
            for flag in ("--help", "-h"):
                with self.subTest(flag=flag):
                    result = subprocess.run(
                        [str(SCRIPT), "plugin", "install", flag],
                        cwd=directory, text=True, capture_output=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, "")
                    self.assertIn("Usage: ww plugin install <slug-or-search> [version]", result.stdout)
                    self.assertIn("v to choose a version", result.stdout)

    def test_missing_exact_slug_404_falls_back_to_search(self):
        error = urllib.error.HTTPError("https://example.test", 404, "Not Found", None, None)
        self.addCleanup(error.close)
        with patch.object(ww.urllib.request, "urlopen", side_effect=error):
            self.assertFalse(ww.exact_plugin("partial-slug"))
        self.assertTrue(error.closed)

    def test_directory_error_response_is_closed_and_reported(self):
        error = urllib.error.HTTPError("https://example.test", 503, "Unavailable", None, None)
        self.addCleanup(error.close)
        with patch.object(ww.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(ww.WWError, "plugin directory"):
                ww.exact_plugin("synthetic-plugin")
        self.assertTrue(error.closed)

    def test_exact_slug_installs_latest_and_activates(self):
        info = {"slug": "akismet", "name": "Akismet", "version": "5.0"}
        with patch.object(ww, "exact_plugin", return_value=info), patch.object(ww.sys.stdin, "isatty", return_value=False), patch.object(ww, "search_plugins") as search, patch.object(ww, "run") as run:
            ww.plugin_install(Path("/site"), "akismet", None)
        search.assert_not_called()
        self.assertEqual(run.call_args.args[0], ["lando", "wp", "plugin", "install", "akismet", "--activate"])

    def test_exact_slug_opens_single_result_picker_for_current_version(self):
        info = {"slug": "hello-dolly", "name": "Hello Dolly", "version": "1.7.2", "rating": 76, "num_ratings": 100}
        with fake_picker(["enter"]) as output, patch.object(ww, "exact_plugin", return_value=info), patch.object(ww, "search_plugins") as search, patch.object(ww, "plugin_versions") as versions, patch.object(ww, "run") as run:
            ww.plugin_install(Path("/site"), "hello-dolly", None)
        self.assertIn("WordPress.org plugins   1/1", output.getvalue())
        self.assertIn("Hello Dolly", output.getvalue())
        search.assert_not_called()
        versions.assert_not_called()
        self.assertEqual(run.call_args.args[0], ["lando", "wp", "plugin", "install", "hello-dolly", "--activate"])

    def test_exact_slug_version_picker_installs_chosen_version(self):
        info = {"slug": "hello-dolly", "name": "Hello Dolly", "version": "1.7.2"}
        with fake_picker(["versions", "down", "enter"]) as output, patch.object(ww, "exact_plugin", return_value=info), patch.object(ww, "search_plugins") as search, patch.object(ww, "plugin_versions", return_value=(["1.7.2", "1.7.1"], "1.7.2")) as versions, patch.object(ww, "run") as run:
            ww.plugin_install(Path("/site"), "hello-dolly", None)
        self.assertIn("Versions for Hello Dolly", output.getvalue())
        search.assert_not_called()
        versions.assert_called_once_with("hello-dolly")
        self.assertEqual(run.call_args.args[0], ["lando", "wp", "plugin", "install", "hello-dolly", "--version=1.7.1", "--activate"])

    def test_exact_slug_cancel_does_not_install(self):
        info = {"slug": "hello-dolly", "name": "Hello Dolly", "version": "1.7.2"}
        with fake_picker(["cancel"]), patch.object(ww, "exact_plugin", return_value=info), patch.object(ww, "run") as run:
            with self.assertRaisesRegex(ww.WWError, "cancelled"):
                ww.plugin_install(Path("/site"), "hello-dolly", None)
        run.assert_not_called()

    def test_exact_slug_with_supplied_version_skips_picker(self):
        info = {"slug": "hello-dolly", "name": "Hello Dolly", "version": "1.7.2"}
        with fake_picker([]), patch.object(ww, "exact_plugin", return_value=info), patch.object(ww, "choose_plugin") as picker, patch.object(ww, "run") as run:
            ww.plugin_install(Path("/site"), "hello-dolly", "1.7.1")
        picker.assert_not_called()
        self.assertEqual(run.call_args.args[0], ["lando", "wp", "plugin", "install", "hello-dolly", "--version=1.7.1", "--activate"])

    def test_existing_plugin_activates_or_reinstalls_requested_version(self):
        installed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(ww, "exact_plugin", return_value={"slug": "akismet"}), patch.object(ww.sys.stdin, "isatty", return_value=False), patch.object(ww, "run", return_value=installed) as run:
            ww.plugin_install(Path("/site"), "akismet", None)
            self.assertEqual(run.call_args.args[0], ["lando", "wp", "plugin", "activate", "akismet"])
            ww.plugin_install(Path("/site"), "akismet", "5.0")
            self.assertEqual(run.call_args.args[0], ["lando", "wp", "plugin", "install", "akismet", "--version=5.0", "--force", "--activate"])

    def test_partial_query_picker_preserves_version(self):
        matches = [
            {"name": "Cache One", "slug": "cache-one", "version": "2.0", "rating": 96, "num_ratings": 123, "downloaded": 1000000, "active_installs": 50000, "last_updated": "2026-09-01"},
            {"name": "Cache Two", "slug": "cache-two", "version": "3.0", "rating": 80, "num_ratings": 12, "downloaded": 10000, "active_installs": 1000, "last_updated": "2026-08-15"},
        ]
        with fake_picker(["down", "enter"]) as output, patch.object(ww, "exact_plugin", return_value=False), patch.object(ww, "search_plugins", return_value=matches), patch.object(ww, "run") as run:
            ww.plugin_install(Path("/site"), "cache", "1.2.3")
        self.assertEqual(run.call_args.args[0], ["lando", "wp", "plugin", "install", "cache-two", "--version=1.2.3", "--activate"])
        self.assertIn("cache-one", output.getvalue())
        self.assertIn("96% from 123 ratings", output.getvalue())
        self.assertIn("1,000,000 downloads", output.getvalue())
        self.assertIn("50,000+ active installs", output.getvalue())
        self.assertNotIn("\x1b[?1049", output.getvalue())

    def test_cancel_does_not_install(self):
        matches = [{"name": "One", "slug": "one", "version": "1.0"}]
        with fake_picker(["cancel"]), patch.object(ww, "exact_plugin", return_value=False), patch.object(ww, "search_plugins", return_value=matches), patch.object(ww, "run") as run:
            with self.assertRaisesRegex(ww.WWError, "cancelled"):
                ww.plugin_install(Path("/site"), "oneish", None)
        run.assert_not_called()

    def test_picker_accepts_csi_arrow_keys(self):
        with patch.object(ww.os, "read", side_effect=[b"\x1b", b"[", b"B"]), patch.object(ww.select, "select", return_value=([0], [], [])):
            self.assertEqual(ww._plugin_key(0), "down")

    def test_picker_orders_by_number_of_ratings(self):
        plugins = [{"slug": "low", "num_ratings": 2}, {"slug": "high", "num_ratings": 200}]
        with fake_picker(["enter"]) as output:
            self.assertEqual(ww.choose_plugin(plugins), ("high", None))
        self.assertLess(output.getvalue().find("high"), output.getvalue().find("low"))

    def test_enter_installs_current_release_without_fetching_versions(self):
        matches = [{"slug": "cache-one", "name": "Cache One", "version": "3.0"}]
        with fake_picker(["enter"]), patch.object(ww, "exact_plugin", return_value=False), patch.object(ww, "search_plugins", return_value=matches), patch.object(ww, "plugin_versions") as versions, patch.object(ww, "run") as run:
            ww.plugin_install(Path("/site"), "cache", None)
        versions.assert_not_called()
        self.assertEqual(run.call_args.args[0], ["lando", "wp", "plugin", "install", "cache-one", "--activate"])

    def test_version_picker_installs_selected_version_and_activates(self):
        matches = [{"slug": "cache-one", "name": "Cache One", "version": "3.0"}]
        with fake_picker(["versions", "down", "enter"]) as output, patch.object(ww, "exact_plugin", return_value=False), patch.object(ww, "search_plugins", return_value=matches), patch.object(ww, "plugin_versions", return_value=(["3.0", "2.0", "1.0"], "3.0")) as versions, patch.object(ww, "run") as run:
            ww.plugin_install(Path("/site"), "cache", None)
        versions.assert_called_once_with("cache-one")
        self.assertEqual(run.call_args.args[0], ["lando", "wp", "plugin", "install", "cache-one", "--version=2.0", "--activate"])
        self.assertIn("Versions for Cache One", output.getvalue())
        self.assertIn("current release", output.getvalue())

    def test_version_picker_choice_overrides_command_version(self):
        matches = [{"slug": "cache-one", "name": "Cache One", "version": "3.0"}]
        with fake_picker(["versions", "down", "enter"]), patch.object(ww, "exact_plugin", return_value=False), patch.object(ww, "search_plugins", return_value=matches), patch.object(ww, "plugin_versions", return_value=(["3.0", "2.0"], "3.0")), patch.object(ww, "run") as run:
            ww.plugin_install(Path("/site"), "cache", "1.0")
        self.assertEqual(run.call_args.args[0], ["lando", "wp", "plugin", "install", "cache-one", "--version=2.0", "--activate"])

    def test_version_picker_back_returns_to_plugin_list(self):
        matches = [{"slug": "cache-one", "name": "Cache One", "version": "3.0"}, {"slug": "cache-two", "name": "Cache Two", "version": "2.0"}]
        with fake_picker(["versions", "back", "down", "enter"]), patch.object(ww, "exact_plugin", return_value=False), patch.object(ww, "search_plugins", return_value=matches), patch.object(ww, "plugin_versions", return_value=(["3.0", "2.0"], "3.0")), patch.object(ww, "run") as run:
            ww.plugin_install(Path("/site"), "cache", None)
        self.assertEqual(run.call_args.args[0], ["lando", "wp", "plugin", "install", "cache-two", "--activate"])

    def test_version_lookup_failure_does_not_install(self):
        matches = [{"slug": "cache-one", "name": "Cache One", "version": "3.0"}]
        with fake_picker(["versions"]), patch.object(ww, "exact_plugin", return_value=False), patch.object(ww, "search_plugins", return_value=matches), patch.object(ww, "plugin_versions", side_effect=ww.WWError("version lookup failed")), patch.object(ww, "run") as run:
            with self.assertRaisesRegex(ww.WWError, "version lookup failed"):
                ww.plugin_install(Path("/site"), "cache", None)
        run.assert_not_called()

    def test_cancel_from_version_picker_does_not_install(self):
        matches = [{"slug": "cache-one", "name": "Cache One", "version": "3.0"}]
        with fake_picker(["versions", "cancel"]), patch.object(ww, "exact_plugin", return_value=False), patch.object(ww, "search_plugins", return_value=matches), patch.object(ww, "plugin_versions", return_value=(["3.0", "2.0"], "3.0")), patch.object(ww, "run") as run:
            with self.assertRaisesRegex(ww.WWError, "cancelled"):
                ww.plugin_install(Path("/site"), "cache", None)
        run.assert_not_called()

    def test_version_lookup_sorts_published_versions(self):
        info = {"slug": "cache-one", "version": "2.0", "versions": {"1.9": "url", "trunk": "url", "1.10": "url", "2.0-beta": "url", "2.0": "url"}}
        with patch.object(ww, "_plugin_information", return_value=info) as request:
            versions, current = ww.plugin_versions("cache-one")
        self.assertEqual(versions, ["2.0", "2.0-beta", "1.10", "1.9"])
        self.assertEqual(current, "2.0")
        request.assert_called_once_with("cache-one", include_versions=True)

    def test_picker_shows_compact_scrolling_window(self):
        plugins = [{"slug": f"plugin-{index}", "name": f"Plugin {index}"} for index in range(10)]
        output = FakeTTYOutput()
        with patch.object(ww.sys, "stdout", output), patch.object(ww, "_plugin_key", side_effect=["down"] * 7 + ["enter"]), patch.object(ww.shutil, "get_terminal_size", return_value=(80, 24)):
            self.assertEqual(ww._plugin_picker(plugins, 0), ("install", 7))
        first_frame = output.getvalue().split("\x1b[15F")[0]
        last_frame = output.getvalue().split("\x1b[15F")[-1]
        self.assertIn("Plugin 6", first_frame)
        self.assertNotIn("Plugin 7", first_frame)
        self.assertIn("Plugin 7", last_frame)
        self.assertNotIn("Plugin 0", last_frame)

    def test_no_results_and_search_failure_do_not_install(self):
        with patch.object(ww, "exact_plugin", return_value=False), patch.object(ww, "search_plugins", return_value=[]), patch.object(ww, "run") as run:
            with self.assertRaisesRegex(ww.WWError, "No WordPress.org plugins"):
                ww.plugin_install(Path("/site"), "missing", None)
        run.assert_not_called()
        with patch.object(ww, "exact_plugin", side_effect=ww.WWError("network failed")), patch.object(ww, "run") as run:
            with self.assertRaisesRegex(ww.WWError, "network failed"):
                ww.plugin_install(Path("/site"), "missing", None)
        run.assert_not_called()

    def test_noninteractive_search_never_selects(self):
        matches = [{"name": "One", "slug": "one", "version": "1.0"}]
        with patch.object(ww, "exact_plugin", return_value=False), patch.object(ww, "search_plugins", return_value=matches), patch.object(ww.sys.stdin, "isatty", return_value=False), patch.object(ww, "run") as run, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ww.WWError, "interactive terminal"):
                ww.plugin_install(Path("/site"), "oneish", None)
        run.assert_not_called()

    def test_search_requests_first_ten_json_results(self):
        result = subprocess.CompletedProcess([], 0, stdout='[{"name":"One","slug":"one","version":"1.0"}]', stderr="")
        with patch.object(ww, "run", return_value=result) as run:
            self.assertEqual(ww.search_plugins(Path("/site"), "partial")[0]["slug"], "one")
        self.assertIn("--per-page=10", run.call_args.args[0])
        self.assertIn("--format=json", run.call_args.args[0])
        self.assertIn("rating,num_ratings,downloaded,active_installs,last_updated", run.call_args.args[0][6])


class PluginListTests(unittest.TestCase):
    def test_action_keys_are_recognized(self):
        for char, action in ((b"a", "activate"), (b"d", "deactivate"), (b"r", "remove")):
            with self.subTest(char=char), patch.object(ww.os, "read", return_value=char):
                self.assertEqual(ww._plugin_key(0), action)

    def test_reads_installed_plugins_as_json(self):
        plugins = [{"name": "akismet", "title": "Akismet", "status": "inactive", "version": "5.0"}]
        result = subprocess.CompletedProcess([], 0, stdout=json.dumps(plugins), stderr="")
        with patch.object(ww, "run", return_value=result) as run:
            self.assertEqual(ww.installed_plugins(Path("/site")), plugins)
        self.assertEqual(run.call_args.args[0][:4], ["lando", "wp", "plugin", "list"])
        self.assertIn("--format=json", run.call_args.args[0])

    def test_activate_and_deactivate_refresh_status(self):
        inactive = {"name": "akismet", "title": "Akismet", "status": "inactive", "version": "5.0"}
        active = {**inactive, "status": "active"}
        with fake_picker(["activate", "deactivate", "cancel"]) as output, patch.object(ww, "installed_plugins", side_effect=[[inactive], [active], [inactive]]) as listed, patch.object(ww, "run") as run:
            ww.plugin_list(Path("/site"))
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["lando", "wp", "plugin", "activate", "akismet"],
            ["lando", "wp", "plugin", "deactivate", "akismet"],
        ])
        self.assertEqual(listed.call_count, 3)
        self.assertIn("Akismet", output.getvalue())
        self.assertIn("v5.0", output.getvalue())
        self.assertNotIn("\x1b[?1049", output.getvalue())

    def test_remove_inactive_plugin_requires_confirmation(self):
        inactive = {"name": "akismet", "status": "inactive", "version": "5.0"}
        with fake_picker(["remove", "back", "cancel"]), patch.object(ww, "installed_plugins", return_value=[inactive]), patch.object(ww, "run") as run:
            ww.plugin_list(Path("/site"))
        run.assert_not_called()

        with fake_picker(["remove", "enter"]), patch.object(ww, "installed_plugins", side_effect=[[inactive], []]), patch.object(ww, "run") as run:
            ww.plugin_list(Path("/site"))
        self.assertEqual([call.args[0] for call in run.call_args_list], [["lando", "wp", "plugin", "delete", "akismet"]])

    def test_remove_active_plugin_deactivates_before_deleting(self):
        active = {"name": "akismet", "status": "active", "version": "5.0"}
        with fake_picker(["remove", "enter"]), patch.object(ww, "installed_plugins", side_effect=[[active], []]), patch.object(ww, "run") as run:
            ww.plugin_list(Path("/site"))
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["lando", "wp", "plugin", "deactivate", "akismet"],
            ["lando", "wp", "plugin", "delete", "akismet"],
        ])

    def test_managed_only_and_action_failure_does_not_delete(self):
        must_use = {"name": "loader", "status": "must-use", "version": "1.0"}
        with fake_picker(["remove", "cancel"]) as output, patch.object(ww, "installed_plugins", return_value=[must_use]), patch.object(ww, "run") as run:
            ww.plugin_list(Path("/site"))
        run.assert_not_called()
        self.assertIn("only regular plugins", output.getvalue())

        active = {"name": "akismet", "status": "active", "version": "5.0"}
        with fake_picker(["remove", "enter", "cancel"]), patch.object(ww, "installed_plugins", return_value=[active]), patch.object(ww, "run", side_effect=ww.WWError("deactivation failed")) as run:
            ww.plugin_list(Path("/site"))
        self.assertEqual(run.call_count, 1)

    def test_noninteractive_list_fails_without_mutation(self):
        with patch.object(ww.sys.stdin, "isatty", return_value=False), patch.object(ww, "installed_plugins") as listed:
            with self.assertRaisesRegex(ww.WWError, "interactive terminal"):
                ww.plugin_list(Path("/site"))
        listed.assert_not_called()

    def test_main_routes_plugin_list(self):
        with patch.object(ww, "project_root", return_value=Path("/site")), patch.object(ww, "plugin_list") as listing:
            self.assertEqual(ww.main(["plugin", "list"]), 0)
        listing.assert_called_once_with(Path("/site"))


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("/sites/example")
        self.marker = {"schema": 1, "name": "example", "title": "Original title"}
        self.info = {
            "title": "Current title", "url": "https://example.lndo.site",
            "admin_url": "https://example.lndo.site/wp-admin/",
            "wordpress": "6.8.3", "php": "8.3.20",
            "database_name": "wordpress", "database_version": "8.0.40",
            "database": {"bytes": "2621440", "tables": "12"},
            "theme": {"name": "Twenty Twenty-Five", "slug": "twentytwentyfive", "version": "1.2"},
            "parent_theme": None, "users": 3, "posts": 2, "pages": 1,
            "multisite": False, "sites": 1, "debug": False,
            "plugins": [
                {"name": "Hello Dolly", "slug": "hello", "version": "1.7.2", "status": "active"},
                {"name": "Akismet", "slug": "akismet", "version": "5.3", "status": "inactive"},
            ],
        }

    def show(self, state="running", result=None):
        projects = [] if state is None else [{"name": "example", "root": str(self.root), "status": state}]
        output = io.StringIO()
        result = result or subprocess.CompletedProcess([], 0, json.dumps(self.info), "")
        with patch.object(ww, "read_marker", return_value=self.marker), patch.object(ww, "list_projects", return_value=projects), patch.object(ww, "run", return_value=result) as run, redirect_stdout(output):
            ww.show_status(self.root)
        return re.sub(r"[ \t]+", " ", output.getvalue()), run

    def test_running_overview_and_single_site_scoped_wp_call(self):
        output, run = self.show()
        for text in (
            "example ● running", "Folder: /sites/example", "Site: Current title",
            "URL: https://example.lndo.site", "Admin: https://example.lndo.site/wp-admin/",
            "WordPress: 6.8.3", "PHP: 8.3.20", "Database: wordpress", "Server: 8.0.40",
            "DB size: 2.5 MiB · 12 tables", "Approximate total of table data + indexes.",
            "Theme: Twenty Twenty-Five 1.2 (twentytwentyfive)", "Users: 3",
            "Content: 2 published posts, 1 published page", "Multisite: no", "Debug: off",
            "Plugins: 2 installed, 1 active, 1 inactive",
            "PLUGIN VERSION STATUS", "Akismet (akismet) 5.3 inactive", "Hello Dolly (hello) 1.7.2 active",
        ):
            self.assertIn(text, output)
        self.assertLess(output.index("Akismet"), output.index("Hello Dolly"))
        self.assertNotIn("Parent theme:", output)
        run.assert_called_once_with(
            ["lando", "wp", "eval", ww.INSTANCE_INFO_PHP, "--skip-plugins", "--skip-themes", "--quiet"],
            self.root, capture=True,
        )

    def test_inactive_instances_do_not_invoke_wordpress_or_start_services(self):
        for state in ("stopped", "partial", None):
            with self.subTest(state=state):
                output, run = self.show(state)
                self.assertIn(f"example ● {state or 'destroyed or not started'}", output)
                self.assertIn("Folder: /sites/example", output)
                self.assertNotIn("DB size:", output)
                run.assert_not_called()

    def test_multisite_child_theme_and_special_plugins(self):
        self.info.update(multisite=True, sites=4, debug=True)
        self.info["parent_theme"] = {"name": "Parent", "slug": "parent", "version": "2.0"}
        self.info["plugins"].extend([
            {"name": "Network", "slug": "network", "version": "1", "status": "network-active"},
            {"name": "Loader", "slug": "loader.php", "version": "", "status": "must-use"},
            {"name": "Cache", "slug": "object-cache.php", "version": "", "status": "drop-in"},
        ])
        output, _ = self.show()
        for text in (
            "Parent theme: Parent 2.0 (parent)", "Users: 3 (this site)", "Multisite: yes (4 sites)",
            "Debug: on", "Plugins: 3 installed, 2 active, 1 inactive, 1 must-use, 1 drop-in",
            "Network (network) 1 network-active", "Loader (loader.php) — must-use",
            "Cache (object-cache.php) — drop-in",
        ):
            self.assertIn(text, output)

    def test_no_plugins_and_zero_database_size(self):
        self.info["plugins"] = []
        self.info["database"] = {"bytes": "0", "tables": "0"}
        output, _ = self.show()
        self.assertIn("Plugins: 0 installed, 0 active, 0 inactive", output)
        self.assertIn("DB size: 0 B", output)

    def test_unavailable_database_metadata_keeps_other_details(self):
        for database in (None, {"bytes": None}, {"bytes": "invalid"}):
            with self.subTest(database=database):
                self.info["database"] = database
                output, _ = self.show()
                self.assertIn("DB size: unavailable", output)
                self.assertIn("Theme: Twenty Twenty-Five", output)
                self.assertIn("Users: 3", output)

    def test_status_and_info_are_synonyms_and_keep_runtime_output_on_wp_failure(self):
        projects = [{"name": "example", "root": str(self.root), "status": "running"}]
        for command in ("status", "info"):
            with self.subTest(command=command):
                output, error = io.StringIO(), io.StringIO()
                with patch.object(ww, "project_root", return_value=self.root), patch.object(ww, "read_marker", return_value=self.marker), patch.object(ww, "list_projects", return_value=projects), patch.object(ww, "instance_info", side_effect=ww.WWError("Database connection failed")), redirect_stdout(output), patch.object(ww.sys, "stderr", error):
                    self.assertEqual(ww.main([command]), 1)
                plain = re.sub(r"[ \t]+", " ", output.getvalue())
                self.assertIn("example ● running", plain)
                self.assertIn("Folder: /sites/example", plain)
                self.assertIn("WordPress: unavailable", plain)
                self.assertIn("Database connection failed", error.getvalue())

    def test_aliases_resolve_site_from_child_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".ww.json").write_text(json.dumps(self.marker))
            (root / ".lando.yml").write_text("name: example\n")
            child = root / "wordpress" / "wp-content"
            child.mkdir(parents=True)
            for command in ("status", "info"):
                with patch.object(ww.Path, "cwd", return_value=child), patch.object(ww, "show_status") as show:
                    self.assertEqual(ww.main([command]), 0)
                show.assert_called_once_with(root)

    def test_invalid_wp_response_is_a_user_facing_error(self):
        for response in ("not json", "[]", "{}", '{"theme": {}, "plugins": [null]}'):
            with self.subTest(response=response), patch.object(ww, "run", return_value=subprocess.CompletedProcess([], 0, response, "")):
                with self.assertRaises(ww.WWError):
                    ww.instance_info(self.root)

    def test_runtime_failure_still_shows_project_folder(self):
        output = io.StringIO()
        with patch.object(ww, "read_marker", return_value=self.marker), patch.object(ww, "list_projects", side_effect=ww.WWError("Docker is unavailable")), redirect_stdout(output):
            with self.assertRaisesRegex(ww.WWError, "Could not determine instance state"):
                ww.show_status(self.root)
        plain = re.sub(r"[ \t]+", " ", output.getvalue())
        self.assertIn("example ● unknown", plain)
        self.assertIn("Folder: /sites/example", plain)

    def test_site_values_cannot_insert_terminal_control_characters(self):
        self.info["title"] = "Title\nForged line\x1b"
        self.info["plugins"][0]["name"] = "Hello\nDolly\x1b"
        output, _ = self.show()
        self.assertIn("Site: TitleForged line", output)
        self.assertIn("HelloDolly", output)
        self.assertNotIn("\x1b", output)

    def test_human_readable_database_sizes(self):
        for size, expected in ((512, "512 B"), (1024, "1.0 KiB"), (1024 ** 3, "1.0 GiB"), (1024 ** 4, "1.0 TiB")):
            self.assertEqual(ww.format_bytes(size), expected)


class LifecycleTests(unittest.TestCase):
    def test_invalid_database_paths_fail_before_lando_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "site"
            root.mkdir()
            (root / ".ww.json").write_text(json.dumps({"schema": 1, "name": "site", "title": "Site"}))
            outside = Path(directory) / "outside"
            outside.mkdir()
            sentinel = outside / "keep"
            sentinel.write_text("preserve")
            for kind in ("symlink", "file", "broken-symlink"):
                db = root / "db"
                if kind == "file":
                    db.write_text("preserve")
                else:
                    db.symlink_to(outside if kind == "symlink" else outside / "missing")
                for action in (ww.reset_db, ww.destroy):
                    with self.subTest(kind=kind, action=action.__name__), patch.object(ww, "confirm", return_value=True), patch.object(ww, "run") as run:
                        with self.assertRaises(ww.WWError):
                            action(root)
                        run.assert_not_called()
                        self.assertEqual(sentinel.read_text(), "preserve")
                db.unlink()

    def test_malformed_docker_inspection_cannot_crash_discovery(self):
        for value in (None, {}, [], [None], [{"Mounts": None}], [{"Mounts": {}}], [{"Mounts": [None, "bad"]}]):
            with self.subTest(value=value), patch.object(ww, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(value), "")):
                self.assertIsNone(ww._container_root({"id": "synthetic"}))
        with tempfile.TemporaryDirectory() as directory:
            value = [{"Mounts": [None, {"Destination": "/app", "Type": "bind", "Source": directory}]}]
            with patch.object(ww, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(value), "")):
                self.assertEqual(ww._container_root({"id": "synthetic"}), Path(directory).resolve())

    def test_unknown_lando_response_is_an_error_not_an_empty_list(self):
        with patch.object(ww, "run", return_value=subprocess.CompletedProcess([], 0, '{"error":"unavailable"}', "")):
            with self.assertRaisesRegex(ww.WWError, "Unexpected"):
                ww._list_records()

    def test_filesystem_failure_and_interrupt_have_useful_exit_codes(self):
        for failure, expected in ((OSError("disk full"), 1), (KeyboardInterrupt(), 130)):
            with self.subTest(expected=expected), patch.object(ww, "create", side_effect=failure), patch.object(ww.sys, "stderr", io.StringIO()) as error:
                self.assertEqual(ww.main(["create"]), expected)
                self.assertNotIn("Traceback", error.getvalue())
                self.assertIn("retrying", error.getvalue())

    def test_destroy_cleanup_failure_reports_retained_state(self):
        with patch.object(ww, "database_path"), patch.object(ww, "confirm", return_value=True), patch.object(ww, "run"), patch.object(ww, "remove_db", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(ww.WWError, "Environment destroyed, but local database cleanup failed"):
                ww.destroy(Path("/synthetic"))

    def test_wp_arguments_and_exit_status_are_forwarded(self):
        result = subprocess.CompletedProcess([], 7)
        with patch.object(ww, "project_root", return_value=Path("/site")), patch.object(ww, "run", return_value=result) as run:
            status = ww.main(["wp", "plugin", "list", "--status=active"])
        self.assertEqual(status, 7)
        self.assertEqual(run.call_args.args[0], ["lando", "wp", "plugin", "list", "--status=active"])
        self.assertFalse(run.call_args.kwargs["check"])

    def test_list_groups_running_and_stopped_ww_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".ww.json").write_text(json.dumps({"schema": 1, "name": "ww-site-123", "title": "Site"}))
            records = [
                {"app": "ww-site-123", "name": "web", "status": "Up 2 minutes"},
                {"app": "ww-site-123", "name": "db", "status": "Up 2 minutes"},
            ]
            with patch.object(ww, "_list_records", return_value=records), patch.object(ww, "_container_root", return_value=root):
                self.assertEqual(ww.list_projects()[0]["status"], "running")
            records[0]["status"] = "Exited (0)"
            records[1]["status"] = "Exited (0)"
            with patch.object(ww, "_list_records", return_value=records), patch.object(ww, "_container_root", return_value=root):
                self.assertEqual(ww.list_projects()[0]["status"], "stopped")

    def test_list_uses_lando_source_path_and_normalized_app_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".ww.json").write_text(json.dumps({"schema": 1, "name": "ww-my-site-123", "title": "Site"}))
            record = {"app": "wwmysite123", "src": [str(root / ".lando.yml")], "running": False}
            with patch.object(ww, "_list_records", return_value=[record]), patch.object(ww, "run") as run:
                projects = ww.list_projects()
            self.assertEqual(projects[0]["status"], "stopped")
            self.assertEqual(projects[0]["root"], str(root.resolve()))
            run.assert_not_called()

    def test_running_completion_includes_partially_running_sites(self):
        projects = [
            {"name": "site-a", "status": "running", "root": "/sites/a"},
            {"name": "site-b", "status": "partial", "root": "/sites/b"},
            {"name": "site-c", "status": "stopped", "root": "/sites/c"},
        ]
        output = io.StringIO()
        with patch.object(ww, "list_projects", return_value=projects), redirect_stdout(output):
            self.assertEqual(ww.main(["__complete_running"]), 0)
        self.assertEqual(output.getvalue().splitlines(), ["site-a\t/sites/a", "site-b\t/sites/b"])

    def test_stop_all_from_unrelated_directory_only_stops_active_ww_sites(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            roots = {}
            records = []
            for name, states in (("active", (True, True)), ("partial", (True, False)), ("stopped", (False, False)), ("other-lando", (True,))):
                root = base / name
                root.mkdir()
                roots[name] = root
                (root / ".lando.yml").write_text(f"name: {name}\n")
                if name != "other-lando":
                    (root / ".ww.json").write_text(json.dumps({"schema": 1, "name": name, "title": name}))
                records.extend({"app": name, "src": [str(root / ".lando.yml")], "running": state} for state in states)
            records.append({"kind": "service", "app": "active", "src": [str(roots["active"] / ".lando.yml")], "running": True})
            unrelated = base / "unrelated"
            unrelated.mkdir()
            log = base / "stops.jsonl"
            lando = base / "lando"
            lando.write_text(f"""#!{os.sys.executable}
import json
import os
import sys
if sys.argv[1:] == ['list', '--all', '--format=json']:
    print(os.environ['WW_TEST_RECORDS'])
elif sys.argv[1:] == ['stop']:
    assert os.path.isfile('.ww.json')
    with open(os.environ['WW_TEST_STOPS'], 'a') as log:
        log.write(json.dumps(os.getcwd()) + '\\n')
else:
    sys.exit(9)
""")
            lando.chmod(0o755)
            result = subprocess.run(
                [str(SCRIPT), "stop-all"], cwd=unrelated, text=True, capture_output=True,
                env={**os.environ, "PATH": f"{base}:" + os.environ["PATH"], "WW_TEST_RECORDS": json.dumps(records), "WW_TEST_STOPS": str(log)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([json.loads(line) for line in log.read_text().splitlines()], [str(roots["active"]), str(roots["partial"])])
            self.assertIn("Stopped 2 ww sites.", result.stdout)
            self.assertEqual(result.stderr, "")

    def test_stop_all_without_running_sites_does_not_stop_anything(self):
        for projects in ([], [{"name": "site", "status": "stopped", "root": "/site"}]):
            with self.subTest(projects=projects), patch.object(ww, "list_projects", return_value=projects), patch.object(ww, "run") as run, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(ww.main(["stop-all"]), 0)
                self.assertIn("No running ww sites found.", output.getvalue())
                run.assert_not_called()

    def test_stop_all_continues_after_failure_and_returns_error(self):
        projects = [{"name": name, "status": "running", "root": f"/sites/{name}"} for name in ("a", "b", "c")]
        with patch.object(ww, "list_projects", return_value=projects), patch.object(ww, "project_root", side_effect=lambda root: root), patch.object(ww, "run", side_effect=[ww.WWError("stop failed"), None, ww.WWError("another failure")]) as run, redirect_stdout(io.StringIO()) as output, patch.object(ww.sys, "stderr", io.StringIO()) as error:
            self.assertEqual(ww.main(["stop-all"]), 1)
        self.assertEqual([call.args for call in run.call_args_list], [(["lando", "stop"], Path(item["root"])) for item in projects])
        self.assertIn("Stopped 1 ww site.", output.getvalue())
        self.assertIn("Could not stop a (/sites/a): stop failed", error.getvalue())
        self.assertIn("Could not stop c (/sites/c): another failure", error.getvalue())
        self.assertIn("Failed to stop 2 ww sites: a, c.", error.getvalue())

    def test_stop_all_reports_discovery_failure_without_stopping_sites(self):
        with patch.object(ww, "list_projects", side_effect=ww.WWError("list failed")), patch.object(ww, "run") as run, patch.object(ww.sys, "stderr", io.StringIO()) as error:
            self.assertEqual(ww.main(["stop-all"]), 1)
        self.assertIn("list failed", error.getvalue())
        run.assert_not_called()

    def test_stop_all_rejects_arguments(self):
        with patch.object(ww, "list_projects") as projects, redirect_stdout(io.StringIO()):
            self.assertEqual(ww.main(["stop-all", "site-a"]), 2)
        projects.assert_not_called()

    def test_stop_and_destroy_accept_a_named_site_from_another_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = (Path(directory) / "site-a").resolve()
            root.mkdir()
            (root / ".ww.json").write_text(json.dumps({"schema": 1, "name": "site-a", "title": "Site A"}))
            (root / ".lando.yml").write_text('name: "site-a"\n')
            (root / "db").mkdir()
            projects = [{"name": "site-a", "status": "running", "root": str(root)}]
            with patch.object(ww, "list_projects", return_value=projects), patch.object(ww, "run") as run, patch.object(ww, "confirm", return_value=True), redirect_stdout(io.StringIO()):
                self.assertEqual(ww.main(["stop", "site-a"]), 0)
                self.assertEqual(run.call_args.args, (["lando", "stop"], root))
                self.assertEqual(ww.main(["destroy", "site-a"]), 0)
                self.assertEqual(run.call_args.args, (["lando", "destroy", "-y"], root))
            self.assertFalse((root / "db").exists())

    def test_named_site_rejects_unknown_name(self):
        with patch.object(ww, "list_projects", return_value=[]), patch.object(ww, "run") as run, redirect_stdout(io.StringIO()), patch.object(ww.sys, "stderr", io.StringIO()) as error:
            self.assertEqual(ww.main(["stop", "missing"]), 1)
        self.assertIn("No ww environment named 'missing'", error.getvalue())
        run.assert_not_called()

    def test_destroy_removes_db_but_keeps_source_and_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "db").mkdir()
            (root / "db" / "data").write_text("db")
            (root / "wordpress").mkdir()
            (root / ".lando.yml").write_text("name: test\n")
            with patch.object(ww, "confirm", return_value=True), patch.object(ww, "run") as run, redirect_stdout(io.StringIO()):
                ww.destroy(root)
            self.assertFalse((root / "db").exists())
            self.assertTrue((root / "wordpress").exists())
            self.assertTrue((root / ".lando.yml").exists())
            self.assertEqual(run.call_args.args[0], ["lando", "destroy", "-y"])

    def test_reset_db_restarts_and_reinstalls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".ww.json").write_text(json.dumps({"schema": 1, "name": "ww-site-123", "title": "Site"}))
            (root / "db").mkdir()
            (root / "db" / "old").write_text("old")
            (root / "wordpress").mkdir()
            (root / "wordpress" / "wp-config.php").write_text("config")
            with patch.object(ww, "confirm", return_value=True), patch.object(ww, "run") as run, redirect_stdout(io.StringIO()):
                ww.reset_db(root)
            self.assertTrue((root / "db").is_dir())
            self.assertFalse((root / "db" / "old").exists())
            calls = [call.args[0] for call in run.call_args_list]
            self.assertEqual(calls[0], ["lando", "stop"])
            self.assertEqual(calls[1], ["lando", "start"])
            self.assertTrue(any(call[:4] == ["lando", "wp", "core", "install"] for call in calls))


@unittest.skipUnless(shutil.which("fish"), "fish is unavailable")
class FishCompletionTests(unittest.TestCase):
    @contextmanager
    def interactive_fish(self, setup=""):
        with tempfile.TemporaryDirectory() as config_dir:
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 160, 0, 0))
            fish_code = f"""
                set -g fish_history ''
                fish_default_key_bindings
                set -p fish_complete_path (pwd)/completions
                function ww
                    printf '\\nEXECUTED:%s\\n' (string join '|' -- $argv)
                end
                {setup}
                function __ww_test_snapshot
                    printf '\\nSNAPSHOT:%s\\n' (string join '|' -- (commandline -op))
                    printf 'BUFFER:%s\\n' (commandline -b)
                    commandline -f repaint
                end
                bind ctrl-t __ww_test_snapshot
                bind --mode insert ctrl-t __ww_test_snapshot
                function fish_prompt
                    printf '\\nWW_PROMPT\\n> '
                end
            """
            def attach_terminal():
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            process = subprocess.Popen(
                ["fish", "--no-config", "-i", "-C", fish_code],
                stdin=slave, stdout=slave, stderr=slave, cwd=SCRIPT.parent,
                env={**os.environ, "TERM": "xterm-256color", "XDG_CONFIG_HOME": config_dir},
                preexec_fn=attach_terminal,
            )
            os.close(slave)

            def send(keys, marker):
                if keys:
                    os.write(master, keys.encode())
                output = bytearray()
                answered_queries = 0
                deadline = time.monotonic() + 5
                while marker.encode() not in output and time.monotonic() < deadline:
                    ready, _, _ = select.select([master], [], [], max(0, deadline - time.monotonic()))
                    if ready:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError:
                            break
                        if not chunk:
                            break
                        output.extend(chunk)
                        # The PTY must act as a terminal: fish waits for its
                        # Primary Device Attribute reply before showing a prompt.
                        # Count in accumulated output so split reads work too.
                        queries = output.count(b"\x1b[0c")
                        if queries > answered_queries:
                            os.write(master, b"\x1b[?1;2c" * (queries - answered_queries))
                            answered_queries = queries
                text = output.decode(errors="replace")
                self.assertIn(marker, text, text)
                return text

            try:
                send("", "WW_PROMPT")
                yield send
            finally:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                os.close(master)

    def test_native_panel_accepts_version_template_as_two_arguments(self):
        for setup in ("", "fish_vi_key_bindings", "bind enter 'printf \"\\nCUSTOM_ENTER\\n\"; commandline -f execute'"):
            with self.subTest(setup=setup), self.interactive_fish(setup) as send:
                output = send(
                    "ww plugin install\t\t\t\t\r\x14",
                    "SNAPSHOT:ww|plugin|install|slug-or-search|version",
                )
                self.assertNotIn("EXECUTED:", output)
                if setup.startswith("bind"):
                    self.assertIn("CUSTOM_ENTER", output)
                output = send("\x15ww plugin install 'cache plugin'\r", "EXECUTED:plugin|install|cache plugin")
                if setup.startswith("bind"):
                    self.assertIn("CUSTOM_ENTER", output)

    def test_template_acceptance_preserves_preceding_process(self):
        with self.interactive_fish() as send:
            output = send(
                "echo before; ww plugin install\t\t\t\t\r\x14",
                "BUFFER:echo before; ww plugin install slug-or-search version ",
            )
            self.assertNotIn("EXECUTED:", output)

    def test_template_binding_preserves_custom_actions_and_is_idempotent(self):
        result = subprocess.run(
            ["fish", "--no-config", "-c", """
                fish_default_key_bindings
                bind enter 'printf "custom handler\\n"; commandline -f execute'
                source completions/ww.fish
                __ww_install_template_bindings
                __ww_install_template_bindings
                bind --user enter
            """],
            cwd=SCRIPT.parent, text=True, capture_output=True, check=True,
        )
        self.assertIn("custom handler", result.stdout)
        self.assertIn("commandline -f execute", result.stdout)
        self.assertEqual(result.stdout.count("__ww_expand_plugin_install_template"), 1)
        self.assertEqual(result.stderr, "")

    def test_plugin_install_native_completion_entries(self):
        result = subprocess.run(
            ["fish", "--no-config", "-c", 'source completions/ww.fish; complete -C "ww plugin install "'],
            cwd=SCRIPT.parent, text=True, capture_output=True, check=True,
        )
        self.assertEqual(result.stdout, (
            "slug-or-search\te.g. akismet or cache\n"
            "slug-or-search version\te.g. akismet 5.0\n"
            "--help\tShow this help\n"
        ))
        self.assertEqual(result.stderr, "")

    def test_plugin_install_completes_real_options_and_leaves_queries_alone(self):
        for line, expected in (
            ("ww plugin install --h", "--help\tShow this help\n"),
            ("ww plugin install akismet", ""),
            ("ww plugin install cache", ""),
        ):
            with self.subTest(line=line):
                result = subprocess.run(
                    ["fish", "--no-config", "-c", 'source completions/ww.fish; complete -C "$argv[1]"', "--", line],
                    cwd=SCRIPT.parent, text=True, capture_output=True, check=True,
                )
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, "")

    def test_command_and_plugin_completion(self):
        result = subprocess.run(
            ["fish", "--no-config", "-c", 'source completions/ww.fish; complete -C "ww "; complete -C "ww plugin "'],
            cwd=SCRIPT.parent, text=True, capture_output=True, check=True,
        )
        self.assertIn("create\tCreate a WordPress site", result.stdout)
        self.assertIn("stop-all\tStop all running ww sites", result.stdout)
        self.assertIn("status\tShow instance overview", result.stdout)
        self.assertIn("info\tShow instance overview (alias of status)", result.stdout)
        self.assertIn("install\tInstall and activate a plugin", result.stdout)
        self.assertIn("list\tManage installed plugins", result.stdout)

    def test_wp_completion_handoff(self):
        fish_code = (
            'function ww; '
            'if test "$argv[1]" = __complete_wp; and test "$argv[2]" = "wp plugin "; '
            'echo activate; end; end; '
            'source completions/ww.fish; complete -C "ww wp plugin "'
        )
        result = subprocess.run(
            ["fish", "--no-config", "-c", fish_code],
            cwd=SCRIPT.parent, text=True, capture_output=True, check=True,
        )
        self.assertIn("activate", result.stdout.splitlines())

    def test_wp_completion_through_wrapper_from_site_child_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".ww.json").write_text("{}")
            (root / ".lando.yml").write_text("name: completion-test\n")
            child = root / "wordpress"
            child.mkdir()
            lando = root / "lando"
            lando.write_text(f"""#!{os.sys.executable}
import os
import sys
assert os.path.isfile('.ww.json'), 'completion must run at the site root'
assert sys.argv[1:4] == ['wp', 'cli', 'completions']
line = sys.argv[4].removeprefix('--line=')
assert sys.argv[5] == '--point=' + str(len(line))
candidates = {{
    'wp ': 'core \\nplugin \\npost \\n',
    'wp pl': 'plugin \\n',
    'wp plugin ': 'activate \\ninstall \\nlist \\n',
    'wp plugin list --f': '--field=\\n--fields=\\n--format=\\n',
}}
sys.stdout.write(candidates[line])
""")
            lando.chmod(0o755)
            for line, expected in (
                ("ww wp ", ["core", "plugin", "post"]),
                ("ww wp pl", ["plugin"]),
                ("ww wp plugin ", ["activate", "install", "list"]),
                ("ww wp plugin list --f", ["--field=", "--fields=", "--format="]),
                (f"{shlex.quote(str(SCRIPT))} wp ", ["core", "plugin", "post"]),
                (f"'{SCRIPT}' wp ", ["core", "plugin", "post"]),
                (str(SCRIPT).replace(" ", "\\ ") + " wp ", ["core", "plugin", "post"]),
                ("echo before; ww wp ", ["core", "plugin", "post"]),
                ("ww plugin install wp ", []),
            ):
                with self.subTest(line=line):
                    result = subprocess.run(
                        ["fish", "--no-config", "-c", f'source "{SCRIPT.parent}/completions/ww.fish"; complete -C "$argv[1]"', "--", line],
                        cwd=child, text=True, capture_output=True, check=True,
                        env={**os.environ, "PATH": f"{root}:{SCRIPT.parent}:" + os.environ["PATH"]},
                    )
                    self.assertEqual(result.stdout.splitlines(), expected)
                    self.assertEqual(result.stderr, "")

    def test_wp_native_panel_accepts_command_then_completes_subcommand(self):
        setup = """
            function ww
                if test "$argv[1]" = __complete_wp
                    switch "$argv[2]"
                        case 'wp '
                            printf 'core\\nplugin\\npost\\n'
                        case 'wp core '
                            printf 'version\\n'
                    end
                else
                    printf '\\nEXECUTED:%s\\n' (string join '|' -- $argv)
                end
            end
        """
        with self.interactive_fish(setup) as send:
            output = send("ww wp \t\t\r\x14", "BUFFER:ww wp core ")
            self.assertIn("SNAPSHOT:ww|wp|core", output)
            self.assertNotIn("EXECUTED:", output)
            output = send("\t\x14", "BUFFER:ww wp core version ")
            self.assertIn("SNAPSHOT:ww|wp|core|version", output)
            self.assertNotIn("EXECUTED:", output)

    def test_stop_and_destroy_complete_running_sites(self):
        fish_code = (
            'function ww; '
            'if test "$argv[1]" = __complete_running; '
            'printf "site-a\\t/sites/a\\nsite-b\\t/sites/b\\n"; end; end; '
            'source completions/ww.fish; '
            'complete -C "ww stop "; complete -C "ww destroy "'
        )
        result = subprocess.run(
            ["fish", "--no-config", "-c", fish_code],
            cwd=SCRIPT.parent, text=True, capture_output=True, check=True,
        )
        self.assertEqual(result.stdout.splitlines(), [
            "site-a\t/sites/a", "site-b\t/sites/b",
            "site-a\t/sites/a", "site-b\t/sites/b",
        ])


if __name__ == "__main__":
    unittest.main()
