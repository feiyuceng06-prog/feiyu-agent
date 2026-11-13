# Feiyu Desktop

Electron workbench for the existing Python agent, inspired by the separation of
responsibilities in Hermes Desktop. The shell uses Chromium on every platform.
Linux is the first validated platform; Windows/macOS need native verification.

## Develop

From the repository root, prepare the agent with `./install.sh --cli-only`, then:

```sh
cd desktop
npm ci
npm start -- -w /path/to/workspace
```

Node.js 22+, npm, Python 3.10+, and C/C++ build tools are required for source
builds. `npm ci` rebuilds node-pty for Electron. No Rust or WebKitGTK is needed.
For development the backend resolver uses `FEIYU_BIN`, then the repository
`.venv`, then `feiyu` on PATH. `-w`, `-m`, and `-r` retain their previous meaning.

## Build and install

```sh
python3 scripts/build_backend.py  # frozen Python interpreter + agent + dependencies
npm run pack                    # release/linux-unpacked, or platform equivalent
npm run dist:linux              # .deb and AppImage under release/
```

`../install.sh --desktop` builds this application and installs an independent
copy under `~/.local/share/feiyu-desktop/`, with a `~/.local/bin/feiyu-desktop`
launcher and an application-menu entry. A build failure preserves the previous
launcher. This source-install flow downloads build dependencies; the finished
app does not install packages on launch or require system Python/Node.js.

Python build/runtime dependencies are pinned in `backend-requirements.txt`; npm dependencies are locked in `package-lock.json`.

Build on the oldest supported distribution. The GitHub Actions workflow builds
on Ubuntu 22.04 for Ubuntu 22.04+ x64 and uploads artifacts without publishing a
release. A local build on a newer distro can require a newer glibc. AppImage
still requires compatible system graphics libraries and an appropriate FUSE
setup (or extraction). The `.deb` declares the Electron runtime libraries.

For other platforms, build the backend on that OS and run `npm run dist:win` or
`npm run dist:mac`. Signing and automatic updates are not configured.

## Architecture

```text
Chat/settings renderer + React file/preview panel
    ├── restricted preload capabilities → Electron main process
    │                                      ├── workspace file reads/uploads
    │                                      ├── native dialogs/window/notifications
    │                                      └── node-pty interactive terminal
    └── send/events → Electron process supervisor → Python feiyu --serve
                                                   (JSON lines on stdio)
```

- Python remains authoritative for sessions, models, permissions and tools.
- Electron owns process lifecycle and native operations. Only the top-level
  application document can call IPC. The renderer has no Node access, uses
  context isolation and a sandbox, and cannot navigate to external content.
- The React panel owns directory browsing, filtering and up to eight preview
  tabs. Switching workspaces clears its scoped state and drops stale results.
  Preview is read-only text/code or raster images; HTML is shown as source,
  never executed. Paths and symlinks are checked against the workspace root.
- The terminal is for the person using the app. Agent commands still go through
  the existing Python approval flow; they do not use this terminal.
- The production connection is local IPC/stdio, with no listening port. Remote
  gateways and Hermes Cloud-style connections are not implemented.
- Existing chat/settings UI is retained rather than rewritten wholesale in
  React. The old Tauri sources and browser fixtures remain for compatibility;
  the default installer and packaging flow use Electron.
- Packaged apps resolve only their bundled backend. A damaged installation
  reports an error instead of adopting a different executable on PATH.

`Ctrl Shift E` toggles files/preview. Ctrl + backtick toggles the terminal.
Logs are written to `$XDG_STATE_HOME/feiyu/desktop.log` (normally
`~/.local/state/feiyu/desktop.log`) with private permissions and a size cap.
Set `FEIYU_DESKTOP_STDERR=1` to also see them in the launching terminal.

## Verify

```sh
npm test                        # path boundaries and real subprocess lifecycle
npm run build
npm run test:desktop             # real Electron, frozen backend and PTY; requires a display
FEIYU_TEST_EXECUTABLE="$PWD/release/linux-unpacked/feiyu-desktop" npm run test:desktop
```

The desktop test uses temporary user/config/workspace directories and makes no
model requests. It verifies backend startup, preview, renderer isolation, path
boundaries, PTY output, workspace switching and settings. A screenshot is saved
under `test-results/workbench.png`. Existing Python protocol tests are under
`tests/test_serve.py` in the repository root.
