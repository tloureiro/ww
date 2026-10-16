import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "ww"
loader = importlib.machinery.SourceFileLoader("ww_tricks_cli", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
ww = importlib.util.module_from_spec(spec)
loader.exec_module(ww)

multisite_spec = importlib.util.spec_from_file_location("ww_multisite", REPO / "tricks" / "multisite" / "run.py")
multisite = importlib.util.module_from_spec(multisite_spec)
multisite_spec.loader.exec_module(multisite)


class FakeTTYOutput(io.StringIO):
    def isatty(self):
        return True


def result(code=0, output=""):
    return subprocess.CompletedProcess([], code, stdout=output, stderr="")


class CatalogTests(unittest.TestCase):
    def test_discovers_bundled_trick_and_completes_slug(self):
        tricks = ww.discover_tricks()
        self.assertEqual([trick["slug"] for trick in tricks], ["multisite"])
        self.assertEqual(tricks[0]["name"], "Convert to multisite")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(ww.main(["__complete_tricks"]), 0)
        self.assertIn("multisite\tConvert to multisite", output.getvalue())

    def test_new_folder_appears_without_registry_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "sample-task"
            folder.mkdir()
            (folder / "manifest.json").write_text(json.dumps({"name": "Sample", "description": "Do something useful."}))
            (folder / "run.py").write_text("print('hello')\n")
            with patch.object(ww, "TRICKS_DIR", Path(directory)):
                self.assertEqual(ww.discover_tricks()[0]["slug"], "sample-task")

    def test_invalid_manifest_and_entrypoint_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "broken"
            folder.mkdir()
            (folder / "manifest.json").write_text("{}")
            with patch.object(ww, "TRICKS_DIR", Path(directory)):
                with self.assertRaisesRegex(ww.WWError, "name and description"):
                    ww.discover_tricks()
                (folder / "manifest.json").write_text(json.dumps({"name": "Broken", "description": "No code"}))
                with self.assertRaisesRegex(ww.WWError, "entry point"):
                    ww.discover_tricks()

    def test_picker_selects_a_slug_and_clears_inline_menu(self):
        tricks = [
            {"slug": "first", "name": "First", "description": "First description"},
            {"slug": "second", "name": "Second", "description": "Second description"},
        ]
        output = FakeTTYOutput()
        with ExitStack() as stack:
            stack.enter_context(patch.object(ww.sys.stdin, "isatty", return_value=True))
            stack.enter_context(patch.object(ww.sys.stdin, "fileno", return_value=0))
            stack.enter_context(patch.object(ww.sys, "stdout", output))
            stack.enter_context(patch.object(ww.termios, "tcgetattr", return_value=[]))
            restore = stack.enter_context(patch.object(ww.termios, "tcsetattr"))
            stack.enter_context(patch.object(ww.tty, "setcbreak"))
            stack.enter_context(patch.object(ww, "_plugin_key", side_effect=["down", "enter"]))
            stack.enter_context(patch.object(ww.shutil, "get_terminal_size", return_value=(100, 24)))
            self.assertEqual(ww.choose_trick(tricks), "second")
        self.assertIn("Second description", output.getvalue())
        self.assertIn("second", output.getvalue())
        self.assertNotIn("\x1b[?1049", output.getvalue())
        restore.assert_called_once()

    def test_picker_cancel_and_noninteractive_behavior(self):
        tricks = [{"slug": "one", "name": "One", "description": "Example"}]
        output = FakeTTYOutput()
        with ExitStack() as stack:
            stack.enter_context(patch.object(ww.sys.stdin, "isatty", return_value=True))
            stack.enter_context(patch.object(ww.sys.stdin, "fileno", return_value=0))
            stack.enter_context(patch.object(ww.sys, "stdout", output))
            stack.enter_context(patch.object(ww.termios, "tcgetattr", return_value=[]))
            stack.enter_context(patch.object(ww.termios, "tcsetattr"))
            stack.enter_context(patch.object(ww.tty, "setcbreak"))
            stack.enter_context(patch.object(ww, "_plugin_key", return_value="cancel"))
            stack.enter_context(patch.object(ww.shutil, "get_terminal_size", return_value=(100, 24)))
            self.assertIsNone(ww.choose_trick(tricks))
        with patch.object(ww.sys.stdin, "isatty", return_value=False):
            with self.assertRaisesRegex(ww.WWError, "interactive terminal"):
                ww.choose_trick(tricks)

    def test_direct_and_menu_runs_stream_and_return_exit_status(self):
        root = Path("/site")
        with patch.object(ww, "run", return_value=result(7)) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(ww.show_tricks(root, "multisite"), 7)
        self.assertEqual(run.call_args.args[0], [ww.sys.executable, str(REPO / "tricks" / "multisite" / "run.py"), "/site"])
        self.assertEqual(run.call_args.args[1], root)
        self.assertFalse(run.call_args.kwargs["check"])
        self.assertNotIn("capture", run.call_args.kwargs)

        with patch.object(ww, "choose_trick", return_value="multisite") as picker, patch.object(ww, "run", return_value=result()) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(ww.show_tricks(root), 0)
        picker.assert_called_once()
        run.assert_called_once()

    def test_unknown_slug_and_main_route(self):
        with self.assertRaisesRegex(ww.WWError, "Available: multisite"):
            ww.show_tricks(Path("/site"), "unknown")
        with patch.object(ww, "project_root", return_value=Path("/site")), patch.object(ww, "show_tricks", return_value=3) as show:
            self.assertEqual(ww.main(["tricks", "multisite"]), 3)
        show.assert_called_once_with(Path("/site"), "multisite")

    @unittest.skipUnless(shutil.which("fish"), "fish is unavailable")
    def test_fish_completes_tricks_and_dynamic_slug(self):
        env = {**os.environ, "PATH": str(REPO) + os.pathsep + os.environ.get("PATH", "")}
        command = 'source completions/ww.fish; complete -C "ww "; complete -C "ww tricks "'
        output = subprocess.run(["fish", "--no-config", "-c", command], cwd=REPO, env=env, text=True, capture_output=True, check=True).stdout
        self.assertIn("tricks\tRun a reusable WordPress task", output)
        self.assertIn("multisite\tConvert to multisite", output)


class MultisiteTests(unittest.TestCase):
    def site(self, root):
        (root / ".ww.json").write_text("{}")
        (root / ".lando.yml").write_text("recipe: wordpress\n")
        webroot = root / "wordpress"
        webroot.mkdir()
        (webroot / "wp-config.php").write_text("<?php\n")
        return webroot

    def preflight_results(self, *, apache=True, network=False, home="https://site.lndo.site"):
        return [
            result(0, json.dumps({"appserver": {"via": "apache" if apache else "nginx"}})),
            result(),
            result(0 if network else 1),
            result(0, home + "\n"),
        ]

    def test_preflight_prepares_rewrite_and_checks_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            webroot = self.site(root)
            (webroot / ".htaccess").write_text("Custom before\n# BEGIN WordPress\nOld rules\n# END WordPress\nCustom after\n")
            with patch.object(multisite, "lando", side_effect=self.preflight_results()) as lando:
                home, path, content = multisite.preflight(root)
            self.assertEqual(home, "https://site.lndo.site")
            self.assertEqual(path, webroot / ".htaccess")
            self.assertTrue(content.startswith("Custom before\n# BEGIN WordPress"))
            self.assertTrue(content.endswith("# END WordPress\nCustom after\n"))
            self.assertIn("RewriteRule . index.php [L]", content)
            self.assertEqual(lando.call_args_list[2].args[1:], ("wp", "core", "is-installed", "--network"))

    def test_preflight_rejects_already_multisite_and_nonroot_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.site(root)
            with patch.object(multisite, "lando", side_effect=self.preflight_results(network=True)):
                with self.assertRaisesRegex(multisite.TrickError, "already a multisite"):
                    multisite.preflight(root)
            with patch.object(multisite, "lando", side_effect=self.preflight_results(home="https://site.lndo.site/blog")):
                with self.assertRaisesRegex(multisite.TrickError, "root-path"):
                    multisite.preflight(root)

    def test_preflight_rejects_nginx_custom_rewrite_and_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            webroot = self.site(root)
            with patch.object(multisite, "lando", side_effect=self.preflight_results(apache=False)):
                with self.assertRaisesRegex(multisite.TrickError, "Apache"):
                    multisite.preflight(root)
            (webroot / ".htaccess").write_text("Custom rewrite only\n")
            with patch.object(multisite, "lando") as lando:
                with self.assertRaisesRegex(multisite.TrickError, "no single WordPress"):
                    multisite.preflight(root)
            lando.assert_not_called()
            (webroot / ".htaccess").unlink()
            (webroot / ".htaccess-lando").write_text("# override\n")
            with self.assertRaisesRegex(multisite.TrickError, "overrides"):
                multisite.preflight(root)

    def test_confirmation_cancel_and_noninteractive_do_not_convert(self):
        with patch.object(multisite, "preflight", return_value=("https://site.lndo.site", Path("/site/wordpress/.htaccess"), "rules")), patch.object(multisite.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="no"), patch.object(multisite, "lando") as lando, redirect_stdout(io.StringIO()):
            self.assertEqual(multisite.convert(Path("/site")), 0)
        lando.assert_not_called()
        with patch.object(multisite, "preflight", return_value=("https://site.lndo.site", Path("/site/wordpress/.htaccess"), "rules")), patch.object(multisite.sys.stdin, "isatty", return_value=False), patch.object(multisite, "lando") as lando:
            with self.assertRaisesRegex(multisite.TrickError, "interactive terminal"):
                multisite.convert(Path("/site"))
        lando.assert_not_called()

    def test_convert_writes_rewrite_and_verifies_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            webroot = self.site(root)
            old = "Before\n# BEGIN WordPress\nOld\n# END WordPress\nAfter\n"
            (webroot / ".htaccess").write_text(old)
            with patch.object(multisite, "preflight", return_value=("https://site.lndo.site", webroot / ".htaccess", multisite.rewrite_contents(old))), patch.object(multisite.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="yes"), patch.object(multisite, "lando", side_effect=[result(), result()]) as lando, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(multisite.convert(root), 0)
            self.assertIn("Before\n# BEGIN WordPress", (webroot / ".htaccess").read_text())
            self.assertIn("# END WordPress\nAfter", (webroot / ".htaccess").read_text())
            self.assertIn("Network admin: https://site.lndo.site/wp-admin/network/", output.getvalue())
            self.assertEqual(lando.call_args_list[0].args[1:], ("wp", "core", "multisite-convert", "--base=/"))
            self.assertEqual(lando.call_args_list[1].args[1:], ("wp", "core", "is-installed", "--network"))

    def test_conversion_failure_does_not_write_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            webroot = self.site(root)
            with patch.object(multisite, "preflight", return_value=("https://site.lndo.site", webroot / ".htaccess", "rules")), patch.object(multisite.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="yes"), patch.object(multisite, "lando", return_value=result(1)), redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(multisite.TrickError, "conversion failed"):
                    multisite.convert(root)
            self.assertFalse((webroot / ".htaccess").exists())

    def test_rewrite_failure_after_conversion_reports_partial_state(self):
        with patch.object(multisite, "preflight", return_value=("https://site.lndo.site", Path("/site/wordpress/.htaccess"), "rules")), patch.object(multisite.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="yes"), patch.object(multisite, "lando", return_value=result()), patch.object(multisite, "write_rewrite", side_effect=OSError("disk full")), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(multisite.TrickError, "conversion completed, but .htaccess could not be updated"):
                multisite.convert(Path("/site"))

    def test_network_check_failure_after_conversion_reports_partial_state(self):
        with patch.object(multisite, "preflight", return_value=("https://site.lndo.site", Path("/site/wordpress/.htaccess"), "rules")), patch.object(multisite.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="yes"), patch.object(multisite, "lando", side_effect=[result(), result(1)]), patch.object(multisite, "write_rewrite"), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(multisite.TrickError, "network check failed"):
                multisite.convert(Path("/site"))


if __name__ == "__main__":
    unittest.main()
