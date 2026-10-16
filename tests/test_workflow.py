"""Real ww processes against a stateful, strictly synthetic Lando executable."""

import json
import os
import pty
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "ww"
FAKE_LANDO = r'''
import json
import os
import sys
from pathlib import Path

base = Path(os.environ['WW_TEST_ROOT']).resolve()
root = Path.cwd().resolve()
assert base in root.parents or root == base, 'outside synthetic fixture'
args = sys.argv[1:]
with (base / 'calls.jsonl').open('a') as log:
    log.write(json.dumps({'cwd': str(root), 'args': args}) + '\n')
if args[:1] == [os.environ.get('WW_TEST_FAIL')]:
    print('synthetic service failure', file=sys.stderr)
    sys.exit(7)
if args == ['list', '--all', '--format=json']:
    records = []
    for marker in base.glob('*/.ww.json'):
        site = marker.parent
        if (site / '.synthetic-state').exists():
            name = json.loads(marker.read_text())['name']
            records.append({'app': name, 'src': [str(site / '.lando.yml')],
                            'running': (site / '.synthetic-state').read_text() == 'running'})
    print(json.dumps(records))
elif args == ['start']:
    (root / '.synthetic-state').write_text('running')
elif args == ['stop']:
    (root / '.synthetic-state').write_text('stopped')
elif args == ['destroy', '-y']:
    (root / '.synthetic-state').unlink(missing_ok=True)
elif args[:3] == ['wp', 'core', 'download']:
    (root / 'wordpress' / 'index.php').write_text('<?php // synthetic source\n')
elif args[:3] == ['wp', 'config', 'create']:
    (root / 'wordpress' / 'wp-config.php').write_text('<?php // synthetic config\n')
elif args[:3] == ['wp', 'core', 'install']:
    (root / 'db').mkdir(exist_ok=True)
    (root / 'db' / 'synthetic-data').write_text('fresh database')
elif args[:2] == ['wp', 'eval']:
    print(json.dumps({'title': 'Synthetic Site', 'url': 'https://demo.lndo.site',
                     'theme': {}, 'plugins': [], 'database': None}))
elif args[:2] == ['wp', 'echo-args']:
    print(json.dumps(args[2:]))
elif args == ['wp', 'plugin', 'list']:
    print('name\tstatus\tversion\nsynthetic-plugin\tinactive\t0.0.0')
else:
    print('unexpected fake Lando command: ' + repr(args), file=sys.stderr)
    sys.exit(9)
'''


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "demo"
        self.root.mkdir()
        self.away = self.base / "elsewhere"
        self.away.mkdir()
        binaries = self.base / "bin"
        binaries.mkdir()
        for name, body in (("lando", FAKE_LANDO), ("docker", "raise SystemExit('Unexpected Docker call')\n")):
            path = binaries / name
            path.write_text(f"#!{sys.executable}\n" + body)
            path.chmod(0o755)
        self.env = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                    "WW_TEST_ROOT": str(self.base), "PYTHONWARNINGS": "default"}
        self.env.pop("WW_TEST_FAIL", None)

    def cli(self, *args, cwd=None, answer=None, code=0, fail=None):
        env = dict(self.env)
        if fail:
            env["WW_TEST_FAIL"] = fail
        command = [sys.executable, str(SCRIPT), *args]
        if answer is None:
            result = subprocess.run(command, cwd=cwd or self.root, env=env,
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        else:
            master, slave = pty.openpty()
            try:
                with subprocess.Popen(command, cwd=cwd or self.root, env=env, stdin=slave,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
                    os.close(slave)
                    slave = None
                    os.write(master, (answer + "\n").encode())
                    out, err = process.communicate(timeout=10)
                    result = subprocess.CompletedProcess(command, process.returncode, out, err)
            finally:
                os.close(master)
                if slave is not None:
                    os.close(slave)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        if code == 0:
            self.assertEqual(result.stderr, "")
        return result

    def calls(self):
        log = self.base / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_create_inspect_wp_stop_restart_reset_and_destroy_journey(self):
        self.assertIn("Site: https://demo.lndo.site", self.cli("create").stdout)
        child = self.root / "wordpress" / "child"
        child.mkdir()
        self.assertIn("Synthetic Site", self.cli("status", cwd=child).stdout)
        self.assertIn("synthetic-plugin", self.cli("wp", "plugin", "list", cwd=child).stdout)
        value = "space and ; shell syntax $(ignored)"
        self.assertEqual(json.loads(self.cli("wp", "echo-args", value, cwd=child).stdout), [value])
        self.cli("stop", "demo", cwd=self.away)
        count = len(self.calls())
        self.assertIn("stopped", self.cli("info", cwd=child).stdout)
        self.assertTrue(all(call["args"] == ["list", "--all", "--format=json"] for call in self.calls()[count:]))
        self.cli("start", cwd=child)
        self.assertIn("Synthetic Site", self.cli("info", cwd=child).stdout)
        data = self.root / "db" / "synthetic-data"
        data.write_text("old database")
        self.assertIn("Cancelled", self.cli("reset-db", answer="no").stdout)
        self.assertEqual(data.read_text(), "old database")
        self.cli("reset-db", answer="yes")
        self.assertEqual(data.read_text(), "fresh database")
        count = len(self.calls())
        self.assertIn("interactive terminal", self.cli("destroy", code=1).stderr)
        self.assertEqual(len(self.calls()), count)
        self.cli("destroy", answer="yes")
        self.assertFalse((self.root / "db").exists())
        self.assertTrue((self.root / "wordpress" / "wp-config.php").is_file())
        self.assertTrue((self.root / ".ww.json").is_file())
        self.assertIn("No ww environments", self.cli("list", cwd=self.away).stdout)
        self.assertTrue(all(self.base in Path(call["cwd"]).parents for call in self.calls()))

    def test_failed_creation_keeps_files_and_allows_explicit_recovery(self):
        self.assertIn("Project files".lower(), self.cli("create", fail="start", code=1).stderr.lower())
        before = (self.root / ".ww.json").read_bytes()
        self.assertIn("already exists", self.cli("create", code=1).stderr)
        self.assertEqual((self.root / ".ww.json").read_bytes(), before)
        self.cli("start")
        self.cli("wp", "core", "download")
        self.cli("wp", "config", "create", "--dbname=wordpress", "--dbuser=wordpress",
                 "--dbpass=wordpress", "--dbhost=database")
        self.cli("wp", "core", "install", "--url=https://demo.lndo.site", "--title=Demo",
                 "--admin_user=admin", "--admin_password=admin", "--admin_email=a@a.com", "--skip-email")
        self.assertIn("Synthetic Site", self.cli("status").stdout)

    def test_reset_failure_preserves_old_data_or_reports_partial_reset(self):
        self.cli("create")
        data = self.root / "db" / "synthetic-data"
        data.write_text("old database")
        self.cli("reset-db", answer="yes", fail="stop", code=1)
        self.assertEqual(data.read_text(), "old database")
        self.assertIn("may already be deleted", self.cli("reset-db", answer="yes", fail="start", code=1).stderr)
        self.assertFalse(data.exists())
        self.assertTrue((self.root / "wordpress" / "wp-config.php").is_file())
        self.cli("reset-db", answer="yes")
        self.assertEqual(data.read_text(), "fresh database")

    def test_help_bad_arguments_and_missing_site_do_not_invoke_lando(self):
        self.cli("--help", cwd=self.away)
        self.cli("-h", cwd=self.away)
        self.cli("unknown", cwd=self.away, code=2)
        self.cli("status", cwd=self.away, code=1)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
