"""get.sh：一行装好发布版。假的 AppImage、假的 curl，不出网。"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'get.sh'

# 只认 --appimage-extract，解出来的东西跟真包同一个形状
FAKE_IMAGE = r'''#!/usr/bin/env bash
[ "$1" = --appimage-extract ] || exit 9
r=squashfs-root
mkdir -p $r/resources/backend $r/usr/share/icons/hicolor/256x256/apps
printf '#!/usr/bin/env bash\nprintf "desktop:%%s" "$*"\n' > $r/AppRun
cat > $r/resources/backend/feiyu-backend <<'S'
#!/usr/bin/env bash
if [ "${1:-}" = --version ]; then echo 'feiyu @VERSION@'; exit @RC@; fi
printf 'cli:%s' "$*"
S
printf '#!/bin/sh\n' > $r/feiyu-desktop
chmod +x $r/AppRun $r/resources/backend/feiyu-backend $r/feiyu-desktop
printf 'png' > $r/usr/share/icons/hicolor/256x256/apps/feiyu-desktop.png
printf '[Desktop Entry]\nName=Feiyu\nStartupWMClass=feiyu\n' > $r/feiyu.desktop
'''

FAKE_CURL = r'''#!/usr/bin/env bash
url="${@: -1}"
case "$url" in
  https://api.github.com/*)
    [ "${FAKE_API_FAIL:-}" = 1 ] && exit 22
    printf '{"assets":[{"name":"x.deb","browser_download_url":"https://github.com/o/r/releases/download/desktop-v9.5.0/x.deb"},'
    printf '{"browser_download_url":"https://github.com/o/r/releases/download/desktop-v9.5.0/Feiyu-9.5.0.AppImage"}]}' ;;
  */releases/latest)
    printf 'https://github.com/feiyuceng06-prog/feiyu-agent/releases/tag/desktop-v9.6.0' ;;
  *.AppImage)
    out=''
    while [ $# -gt 1 ]; do [ "$1" = -o ] && out=$2; shift; done
    printf '%s\n' "$url" >> "$FAKE_LOG"
    cp "$FAKE_IMAGE" "$out" ;;
  *) exit 22 ;;
esac
'''

FAKE_UNAME = '''#!/bin/sh
case "$1" in -s) echo "${FAKE_OS:-Linux}" ;; -m) echo "${FAKE_ARCH:-x86_64}" ;; esac
'''


def desktop_exec(path):
    s = str(path)
    for a, b in (('\\', '\\\\'), ('"', '\\"'), ('$', '\\$'), ('`', '\\`'), ('%', '%%')):
        s = s.replace(a, b)
    return 'Exec="%s" %%F' % s


class TestGet(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / 'home with spaces $(ignored)'
        self.home.mkdir()
        self.fakebin = self.root / 'bin'
        self.fakebin.mkdir()
        self.fake('curl', FAKE_CURL)
        self.fake('uname', FAKE_UNAME)
        self.log = self.root / 'downloads.log'
        self.env = dict(os.environ, HOME=str(self.home), PATH='%s:/usr/bin:/bin' % self.fakebin,
                        FAKE_LOG=str(self.log), FAKE_IMAGE=str(self.image('9.5.0')))
        self.env['SHELL'] = '/bin/bash'
        for name in ('XDG_DATA_HOME', 'ZDOTDIR', 'FEIYU_NO_MODIFY_PATH'):
            self.env.pop(name, None)
        self.bin = self.home / '.local/bin'
        self.base = self.home / '.local/share/feiyu-desktop'

    def fake(self, name, content):
        path = self.fakebin / name
        path.write_text(content)
        path.chmod(0o755)

    def image(self, version, rc=0):
        path = self.root / ('Feiyu-%s-%d.AppImage' % (version, rc))
        path.write_text(FAKE_IMAGE.replace('@VERSION@', version).replace('@RC@', str(rc)))
        path.chmod(0o755)
        return path

    def get(self, *args, env=None):
        return subprocess.run(['bash', str(SCRIPT), *map(str, args)], env=env or self.env,
                              cwd=self.root, text=True, capture_output=True)

    def run_cmd(self, name, *args):
        return subprocess.run([self.bin / name, *args], env=self.env, text=True,
                              capture_output=True).stdout

    def apps(self):
        return sorted(p.name for p in self.base.glob('app-*'))

    def test_装好两个命令和菜单项(self):
        r = self.get(self.image('9.1.0'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.run_cmd('feiyu', 'hello world'), 'cli:hello world')
        self.assertEqual(self.run_cmd('feiyu-desktop', 'a b'), 'desktop:a b')
        entry = (self.home / '.local/share/applications/feiyu.desktop').read_text()
        self.assertIn(desktop_exec(self.bin / 'feiyu-desktop'), entry)
        self.assertIn('StartupWMClass=feiyu', entry)
        self.assertIn('Icon=feiyu', entry)
        self.assertEqual((self.home / '.local/share/icons/hicolor/256x256/apps/feiyu.png').read_text(), 'png')
        self.assertEqual(self.apps(), ['app-9.1.0'])
        self.assertEqual(list(self.base.glob('.get-*')), [])
        self.assertIn('装好了 Feiyu 9.1.0', r.stdout)

    def test_升级只留这一版和上一版(self):
        for v in ('9.1.0', '9.2.0', '9.3.0'):
            r = self.get(self.image(v))
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.apps(), ['app-9.2.0', 'app-9.3.0'])
        self.assertIn('app-9.3.0', (self.bin / 'feiyu').read_text())

    def test_同一版本重装不留下旧的那份(self):
        self.get(self.image('9.1.0'))
        (self.base / 'app-9.1.0/坏掉的文件').write_text('x')
        r = self.get(self.image('9.1.0'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.apps(), ['app-9.1.0'])
        self.assertFalse((self.base / 'app-9.1.0/坏掉的文件').exists())
        self.assertEqual(list(self.base.glob('.get-*')), [])

    def test_自带的程序起不来就不动原来装的(self):
        self.get(self.image('9.1.0'))
        before = {n: (self.bin / n).read_bytes() for n in ('feiyu', 'feiyu-desktop')}
        r = self.get(self.image('9.2.0', rc=1))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('原来装的没动', r.stderr)
        self.assertEqual({n: (self.bin / n).read_bytes() for n in before}, before)
        self.assertEqual(self.apps(), ['app-9.1.0'])
        self.assertEqual(list(self.base.glob('.get-*')), [])

    def test_别的方式装的_feiyu_不覆盖(self):
        self.bin.mkdir(parents=True)
        (self.bin / 'feiyu').write_text('#!/bin/sh\necho source\n')
        r = self.get(self.image('9.1.0'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.bin / 'feiyu').read_text(), '#!/bin/sh\necho source\n')
        self.assertIn('没有覆盖', r.stderr)
        self.assertEqual(self.run_cmd('feiyu-desktop'), 'desktop:')

    def test_从_Releases_下最新的(self):
        r = self.get()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log.read_text().split(),
                         ['https://github.com/o/r/releases/download/desktop-v9.5.0/Feiyu-9.5.0.AppImage'])
        self.assertEqual(self.apps(), ['app-9.5.0'])

    def test_接口被限流时看最新版跳到哪个标签(self):
        r = self.get(env=dict(self.env, FAKE_API_FAIL='1'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log.read_text().split(), [
            'https://github.com/feiyuceng06-prog/feiyu-agent/releases/download/desktop-v9.6.0/Feiyu-9.6.0.AppImage'])

    def test_不是_x86_64_就不装(self):
        r = self.get(self.image('9.1.0'), env=dict(self.env, FAKE_ARCH='aarch64'))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('x86_64', r.stderr)
        self.assertFalse((self.home / '.local').exists())

    def test_下到一半断了什么都不做(self):
        lines = SCRIPT.read_text().splitlines(keepends=True)
        self.assertEqual(lines[-1].strip(), 'main "$@"')
        for cut in (len(lines) - 1, len(lines) // 2):
            r = subprocess.run(['bash', '-s', '--', str(self.image('9.1.0'))], input=''.join(lines[:cut]),
                               env=self.env, cwd=self.root, text=True, capture_output=True)
            self.assertFalse((self.home / '.local').exists(), (cut, r.stdout, r.stderr))

    def test_PATH_里没有就写进_bashrc_而且只写一次(self):
        for _ in range(2):
            r = self.get(self.image('9.1.0'))
            self.assertEqual(r.returncode, 0, r.stderr)
        rc = self.home / '.bashrc'
        self.assertEqual(rc.read_text().count('由 feiyu 的 get.sh 加'), 1)
        self.assertIn('source ~/.bashrc', r.stdout)
        found = subprocess.run(['bash', '-c', 'PATH=/usr/bin:/bin; . "$HOME/.bashrc"; command -v feiyu'],
                               env=self.env, text=True, capture_output=True).stdout.strip()
        self.assertEqual(found, str(self.bin / 'feiyu'))

    def test_那一行不会让_PATH_越来越长(self):
        self.get(self.image('9.1.0'))
        out = subprocess.run(['bash', '-c', '. "$HOME/.bashrc"; . "$HOME/.bashrc"; printf %s "$PATH"'],
                             env=self.env, text=True, capture_output=True).stdout
        self.assertEqual(out.split(':').count(str(self.bin)), 1)

    def test_zsh_和_fish_写各自的配置(self):
        r = self.get(self.image('9.1.0'), env=dict(self.env, SHELL='/usr/bin/zsh'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('由 feiyu 的 get.sh 加', (self.home / '.zshrc').read_text())
        self.assertFalse((self.home / '.bashrc').exists())
        self.get(self.image('9.1.0'), env=dict(self.env, SHELL='/usr/bin/fish'))
        fish = (self.home / '.config/fish/conf.d/feiyu.fish').read_text()
        self.assertIn('set -gx PATH $HOME/.local/bin $PATH', fish)

    def test_PATH_里已经有就不改配置(self):
        env = dict(self.env, PATH='%s:%s' % (self.bin, self.env['PATH']))
        r = self.get(self.image('9.1.0'), env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.home / '.bashrc').exists())
        self.assertIn('终端版：运行 feiyu', r.stdout)

    def test_FEIYU_NO_MODIFY_PATH_就不改配置(self):
        r = self.get(self.image('9.1.0'), env=dict(self.env, FEIYU_NO_MODIFY_PATH='1'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.home / '.bashrc').exists())
        self.assertIn('不在 PATH 里', r.stderr)

    def test_管道进来也能装(self):
        r = subprocess.run(['bash', '-s', '--', str(self.image('9.1.0'))], input=SCRIPT.read_text(),
                           env=self.env, cwd=self.root, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.run_cmd('feiyu', 'x'), 'cli:x')


if __name__ == '__main__':
    unittest.main()
