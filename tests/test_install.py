"""Installer integration checks in a temporary checkout and home directory."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.checkout = root / 'checkout with spaces $(ignored)'
        self.checkout.mkdir()
        shutil.copy(Path(__file__).resolve().parents[1] / 'install.sh', self.checkout)
        (self.checkout / 'desktop/icons').mkdir(parents=True)
        (self.checkout / 'desktop/icons/256.png').write_bytes(b'icon')
        self.home = root / 'home with spaces'
        self.home.mkdir()
        self.fakebin = root / 'bin'
        self.fakebin.mkdir()
        self.fake('python-fake', '''#!/usr/bin/env bash
case "$1" in
  -c) exit 0 ;;
  -V) echo 'Python 3.12.0'; exit 0 ;;
  -m) if [ "$2" = pip ]; then [ "${FAIL_PIP:-}" != 1 ]; exit $?; fi
      mkdir -p "$3/bin"; cp "$0" "$3/bin/python"
      cat > "$3/bin/feiyu" <<'SCRIPT'
#!/usr/bin/env bash
if [ "${1:-}" = --version ]; then echo 'feiyu 0.test'; else printf 'cli:%s' "$*"; fi
SCRIPT
      chmod +x "$3/bin/feiyu"; exit 0 ;;
esac
''')
        # The venv's Python only needs to report a successful pip install.
        self.fake('pkg-config', '#!/bin/sh\nexit 0\n')
        self.fake('cargo', '''#!/usr/bin/env bash
if [ "${FAIL_CARGO:-}" = 1 ]; then exit 1; fi
mkdir -p target/release
cat > target/release/feiyu-desktop <<'SCRIPT'
#!/usr/bin/env bash
printf '%s' "$FEIYU_BIN"
SCRIPT
chmod +x target/release/feiyu-desktop
''')
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.fakebin) + ':/usr/bin:/bin',
                        FEIYU_PYTHON=str(self.fakebin / 'python-fake'))
        self.env.pop('XDG_DATA_HOME', None)
        self.env.pop('FEIYU_BIN', None)

    def fake(self, name, content):
        path = self.fakebin / name
        path.write_text(content)
        path.chmod(0o755)

    def install(self, *args, env=None):
        return subprocess.run(['bash', 'install.sh', *args], cwd=self.checkout,
                              env=env or self.env, text=True, capture_output=True)

    def test_cli_only_and_space_in_checkout(self):
        result = self.install('--cli-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        cmd = self.home / '.local/bin/feiyu'
        result = subprocess.run([cmd, 'hello world'], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.stdout, 'cli:hello world')
        self.assertFalse((self.home / '.local/bin/feiyu-desktop').exists())

    def test_desktop_launch_and_failed_upgrade_keeps_launcher(self):
        result = self.install('--desktop')
        self.assertEqual(result.returncode, 0, result.stderr)
        launcher = self.home / '.local/bin/feiyu-desktop'
        old = launcher.read_bytes()
        result = subprocess.run([launcher], env=self.env, text=True, capture_output=True)
        backend = Path(result.stdout)
        self.assertTrue(backend.is_file())
        self.assertNotIn(str(self.checkout), result.stdout)
        self.assertFalse((self.home / '.local/bin/feiyu').exists())
        entry = (self.home / '.local/share/applications/feiyu.desktop').read_text()
        self.assertIn('Exec="' + str(launcher) + '" %F', entry)
        env = dict(self.env, FAIL_CARGO='1')
        result = self.install('--desktop', env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(launcher.read_bytes(), old)
        shutil.rmtree(self.checkout)
        result = subprocess.run([launcher], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.stdout, str(backend))

    def test_failed_dependency_install_keeps_existing_cli(self):
        launcher = self.home / '.local/bin/feiyu'
        launcher.parent.mkdir(parents=True)
        launcher.write_text('previous install')
        result = self.install('--cli-only', env=dict(self.env, FAIL_PIP='1'))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(launcher.read_text(), 'previous install')

    def test_help_does_not_install(self):
        result = self.install('--help')
        self.assertEqual(result.returncode, 0)
        self.assertFalse((self.checkout / '.venv').exists())


if __name__ == '__main__':
    unittest.main()
