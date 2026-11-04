#!/usr/bin/env bash
# Install from this checkout; rerun after updating it.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
BIN="${HOME}/.local/bin"
SHIM="$BIN/feiyu"
DESK_SHIM="$BIN/feiyu-desktop"
MODE=cli
PY="${FEIYU_PYTHON:-}"

say()  { printf '\033[38;2;150;103;74m▸\033[0m %s\n' "$1"; }
warn() { printf '\033[38;2;168;132;60m!\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[38;2;176;87;78m✗\033[0m %s\n' "$1" >&2; exit 1; }
usage() {
  cat <<'HELP'
用法: ./install.sh [--cli-only | --desktop | --all] [--python 路径]

默认只安装命令行版。
  --cli-only     只安装命令行版
  --desktop      只安装独立的桌面版（不安装 feiyu 命令）
  --all          分别安装命令行版和桌面版
  --python 路径  指定 Python 3.10 以上的解释器（也可设 FEIYU_PYTHON）
  -h, --help     显示帮助
安装需要联网下载 Python 依赖；不会使用 sudo 或修改 shell 配置。
HELP
}
while (($#)); do
  case "$1" in
    --cli-only) MODE=cli ;;
    --desktop) MODE=desktop ;;
    --all) MODE=all ;;
    --python) shift; (($#)) || die '--python 后需要解释器路径'; PY="$1" ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数：$1（运行 ./install.sh --help 查看用法）" ;;
  esac
  shift
done

if [ -z "$PY" ]; then
  for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      PY="$candidate"; break
    fi
  done
fi
[ -n "$PY" ] || die '需要 Python 3.10 以上；Debian/Ubuntu 可安装 python3 和 python3-venv'
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null || die "Python 不可用或版本低于 3.10：$PY"
say "用 $("$PY" -V 2>&1)"

if [ "$MODE" != desktop ]; then
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  say '创建虚拟环境 .venv'
  "$PY" -m venv "$ROOT/.venv" || die '创建虚拟环境失败；Debian/Ubuntu 请安装 python3-venv 后重试'
fi
say '安装 Python 依赖（包括 OpenAI 兼容接口）'
"$ROOT/.venv/bin/python" -m pip install -e '.[openai]' || die 'Python 依赖安装失败，请检查网络和上方错误后重试'
if ! VERSION="$("$ROOT/.venv/bin/feiyu" --version 2>&1)"; then
  printf '%s\n' "$VERSION" >&2
  die '安装完成但 feiyu 无法启动；已有启动器未改动'
fi
mkdir -p "$BIN"
# %q protects spaces, quotes and shell metacharacters in checkout paths.
printf '#!/usr/bin/env bash\nexec %q "$@"\n' "$ROOT/.venv/bin/feiyu" > "$SHIM"
chmod +x "$SHIM"
say "命令行版已安装：$VERSION"
fi

build_desktop() {
  if ! command -v cargo >/dev/null 2>&1; then
    warn '未找到 cargo；安装 Rust 后可重新运行 ./install.sh --desktop'
    return 1
  fi
  if [ "$(uname -s)" = Linux ] && ! command -v pkg-config >/dev/null 2>&1; then
    warn '未找到 pkg-config，无法检查桌面构建依赖'
    return 1
  fi
  if [ "$(uname -s)" = Linux ] && ! pkg-config --exists gtk+-3.0 webkit2gtk-4.1 libsoup-3.0; then
    warn '缺少 GTK 3、WebKitGTK 4.1 或 libsoup 3 开发包。'
    warn 'Debian/Ubuntu: sudo apt install libgtk-3-dev libwebkit2gtk-4.1-dev libsoup-3.0-dev build-essential pkg-config'
    warn 'Fedora: sudo dnf install gtk3-devel webkit2gtk4.1-devel libsoup3-devel gcc pkgconf-pkg-config'
    warn 'Arch: sudo pacman -S gtk3 webkit2gtk-4.1 libsoup3 base-devel pkgconf'
    return 1
  fi
  say '编译桌面端（首次构建可能需要联网和几分钟）'
  if ! (cd "$ROOT/desktop" && cargo build --release --bin feiyu-desktop); then
    warn '桌面端构建失败；已有桌面启动器保留，命令行版可继续使用'
    return 1
  fi
  local data="${XDG_DATA_HOME:-$HOME/.local/share}/feiyu-desktop"
  mkdir -p "$data" "$BIN" || return 1
  local app
  app=$(mktemp -d "$data/app-XXXXXXXX") || return 1
  say '为桌面版安装独立的 Python 后端'
  if ! "$PY" -m venv "$app/.venv" ||
     ! "$app/.venv/bin/python" -m pip install "$ROOT[openai]" ||
     ! "$app/.venv/bin/feiyu" --version >/dev/null ||
     ! cp "$ROOT/desktop/target/release/feiyu-desktop" "$app/feiyu-desktop"; then
    rm -rf -- "$app"
    warn '桌面后端安装失败；已有桌面版保留'
    return 1
  fi
  printf '#!/usr/bin/env bash\nexport FEIYU_BIN=%q\nexec %q "$@"\n' \
    "$app/.venv/bin/feiyu" "$app/feiyu-desktop" > "$DESK_SHIM" || return 1
  chmod +x "$DESK_SHIM" || return 1
  local apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  local icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
  mkdir -p "$apps" "$icons" || return 1
  cp "$ROOT/desktop/icons/256.png" "$icons/feiyu.png" || return 1
  # Desktop Entry Exec quoting has its own escaping rules, separate from the shell.
  local entry="$DESK_SHIM"
  entry=${entry//\\/\\\\}; entry=${entry//\"/\\\"}; entry=${entry//\$/\\\$}; entry=${entry//\`/\\\`}; entry=${entry//%/%%}
  cat > "$apps/feiyu.desktop" <<DESKTOP_ENTRY
[Desktop Entry]
Type=Application
Name=Feiyu
Comment=Terminal coding agent
Exec="$entry" %F
Icon=feiyu
Terminal=false
Categories=Development;
StartupWMClass=Feiyu
DESKTOP_ENTRY
  [ -s "$apps/feiyu.desktop" ] || return 1
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$apps" >/dev/null 2>&1 || true
  fi
  say '桌面版已安装：feiyu-desktop（也可在应用列表中打开 Feiyu）'
}
if [ "$MODE" != cli ]; then
  if ! build_desktop; then
    die '桌面版未安装；解决上面的依赖或构建错误后重试'
  fi
fi
if [ "$MODE" != desktop ]; then
  case ":${PATH}:" in
    *":${BIN}:"*) ;;
    *) warn "$BIN 不在 PATH；可直接运行 $SHIM，或将 export PATH=\"\$HOME/.local/bin:\$PATH\" 加到 shell 配置中" ;;
  esac
  say '运行 feiyu，然后输入 /key add 配置模型；输入 /help 查看命令。'
fi
