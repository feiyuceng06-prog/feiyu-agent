#!/usr/bin/env bash
# Ubuntu one-command CLI installer. Keeps each successful source tree for its venv.
set -euo pipefail

fail() { printf 'Feiyu: %s\n' "$*" >&2; exit 1; }
[ "$(uname -s)" = Linux ] || fail '此安装入口仅支持 Ubuntu。'
[ -r /etc/os-release ] || fail '无法识别系统；请从源码运行 install.sh。'
. /etc/os-release
[ "${ID:-}" = ubuntu ] || fail '此安装入口仅支持 Ubuntu；其他系统请从源码运行 install.sh。'
command -v tar >/dev/null || fail '需要 tar。'
command -v wget >/dev/null || fail '需要 wget。'

need_packages=()
if ! command -v python3 >/dev/null || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  need_packages+=(python3)
fi
# Debian's Python can have venv installed without ensurepip, so test an actual venv.
probe=$(mktemp -d)
trap 'rm -rf -- "$probe"' EXIT
if ! python3 -m venv "$probe/venv" >/dev/null 2>&1; then
  need_packages+=(python3-venv)
fi
rm -rf -- "$probe"
if ((${#need_packages[@]})); then
  printf '需要安装系统依赖：%s（可能需要输入 sudo 密码）\n' "${need_packages[*]}"
  command -v sudo >/dev/null || fail '未找到 sudo，请安装上述依赖后重试。'
  sudo apt-get update < /dev/tty
  sudo apt-get install -y "${need_packages[@]}" < /dev/tty
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail '需要 Python 3.10 或更新版本。'

base="${XDG_DATA_HOME:-$HOME/.local/share}/feiyu-agent"
mkdir -p "$base"
source_dir=$(mktemp -d "$base/source-XXXXXXXX")
# Keep old installations intact until the new install has passed its startup check.
cleanup() { rm -rf -- "$probe"; [ "${installed:-}" = yes ] || rm -rf -- "$source_dir"; }
trap cleanup EXIT
printf '正在下载 Feiyu Agent...\n'
wget -qO "$source_dir/source.tar.gz" https://github.com/feiyuceng06-prog/feiyu-agent/archive/refs/heads/main.tar.gz || fail '下载失败，请检查网络连接。'
tar -xzf "$source_dir/source.tar.gz" --strip-components=1 -C "$source_dir" || fail '解压失败。'
rm "$source_dir/source.tar.gz"
bash "$source_dir/install.sh" --cli-only
installed=yes
printf '\n安装完成。输入 /key add 添加模型。\n'
if [ -t 1 ] && [ -r /dev/tty ]; then
  printf '正在启动 Feiyu；退出后可运行 ~/.local/bin/feiyu。\n'
  exec "$HOME/.local/bin/feiyu" </dev/tty
fi
printf '运行 ~/.local/bin/feiyu 开始使用。\n'
