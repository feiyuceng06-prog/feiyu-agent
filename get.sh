#!/usr/bin/env bash
# 一行装好 Feiyu（桌面版 + 终端版），不用 sudo、不用 Python：
#
#   curl -fsSL https://raw.githubusercontent.com/feiyuceng06-prog/feiyu-agent/main/get.sh | bash
#
# 装的是 Releases 里最新的 AppImage：不挂载，解开放进用户目录，所以也不要 libfuse2；
# 包里自带的后端就是完整的终端版 feiyu。再跑一遍就是升级。
# 已经有 AppImage 文件（比如在别的机器上下好拷过来）：bash get.sh Feiyu-x.y.z.AppImage
set -euo pipefail

REPO=feiyuceng06-prog/feiyu-agent
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
BASE="$DATA/feiyu-desktop"
BIN="$HOME/.local/bin"
MARK='# 由 feiyu 的 get.sh 安装'

say()  { printf '\033[38;2;150;103;74m▸\033[0m %s\n' "$1"; }
warn() { printf '\033[38;2;168;132;60m!\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[38;2;176;87;78m✗\033[0m %s\n' "$1" >&2; exit 1; }
has()  { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'HELP'
用法: get.sh [AppImage 文件]

不给文件就从 GitHub Releases 下最新的一版。程序装进 ~/.local/share/feiyu-desktop，
feiyu（终端版）和 feiyu-desktop（桌面版）两个命令放在 ~/.local/bin，应用菜单里叫 Feiyu。
不用 sudo，不改 shell 配置；模型、key 和对话记录在 ~/.config/feiyu 和 ~/.local/state/feiyu，
重装、升级都不动它们。
HELP
}

fetch() {
  if has curl; then curl -fsSL "$1"; else wget -qO- "$1"; fi
}

download() {
  if has curl; then
    curl -fL --progress-bar -o "$2" "$1"
  else
    wget -q --show-progress -O "$2" "$1" || wget -q -O "$2" "$1"
  fi
}

landed() {
  if has curl; then
    curl -fsSLI -o /dev/null -w '%{url_effective}' "$1"
  else
    wget -S --spider "$1" 2>&1 | sed -n 's/^ *[Ll]ocation: *//p' | tail -n1 | tr -d '\r'
  fi
}

latest_url() {
  local url tag
  url=$(fetch "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
        | grep -o 'https://[^"]*\.AppImage' | head -n1) || true
  if [ -z "$url" ]; then
    # 接口按出口 IP 限流（一小时 60 次），被限了就看 /releases/latest 跳到了哪个标签
    tag=$(landed "https://github.com/$REPO/releases/latest" 2>/dev/null) || true
    tag=${tag##*/}
    case "$tag" in
      desktop-v[0-9]*) url="https://github.com/$REPO/releases/download/$tag/Feiyu-${tag#desktop-v}.AppImage" ;;
    esac
  fi
  printf '%s' "$url"
}

write_shim() {
  printf '#!/usr/bin/env bash\n%s\nexec %q "$@"\n' "$MARK" "$2" > "$1.tmp"
  chmod +x "$1.tmp"
  mv -f -- "$1.tmp" "$1"
}

main() {
  case "${1:-}" in
    -h|--help) usage; return 0 ;;
    -*) die "未知参数：$1（bash get.sh --help 看用法）" ;;
  esac
  [ "$(uname -s)" = Linux ] || die '现在只有 Linux 的包，macOS 和 Windows 的还没有'
  case "$(uname -m)" in
    x86_64|amd64) ;;
    *) die "现在只有 x86_64 的包，这台是 $(uname -m)；可以从源码装：https://github.com/$REPO" ;;
  esac
  has curl || has wget || die '需要 curl 或 wget'

  mkdir -p "$BASE" "$BIN" || die "建不了 $BASE"
  local tmp
  tmp=$(mktemp -d "$BASE/.get-XXXXXXXX") || die '建不了临时目录'
  # shellcheck disable=SC2064  # 现在就展开：退出时 main 早返回了，局部变量已经没了
  trap "rm -rf -- $(printf %q "$tmp")" EXIT

  local image="$tmp/Feiyu.AppImage"
  if [ -n "${1:-}" ]; then
    [ -f "$1" ] || die "找不到文件：$1"
    cp -- "$1" "$image" || die "读不了 $1"
  else
    local url
    url=$(latest_url)
    [ -n "$url" ] || die '问不到最新版的下载地址（连不上 GitHub？）。也可以在别处下好 AppImage，再 bash get.sh 那个文件'
    say "下载 ${url##*/}"
    download "$url" "$image" || die '下载失败，检查一下网络再重跑'
  fi
  chmod +x "$image"

  say '解开安装包'
  (cd "$tmp" && ./Feiyu.AppImage --appimage-extract >/dev/null 2>&1 </dev/null) \
    || die '解不开这个 AppImage，文件可能没下完整，重跑一遍试试'
  local root="$tmp/squashfs-root"
  { [ -x "$root/AppRun" ] && [ -x "$root/resources/backend/feiyu-backend" ]; } \
    || die '包里缺东西，这不像是 Feiyu 的 AppImage'

  local version
  if ! version=$("$root/resources/backend/feiyu-backend" --version 2>&1 </dev/null); then
    printf '%s\n' "$version" >&2
    die '自带的程序在这台机器上起不来。系统太旧的话（要 glibc 2.35 以上，也就是 Ubuntu 22.04 那一代）请从源码装；原来装的没动'
  fi
  version=${version#feiyu }
  [[ $version =~ ^[0-9][0-9A-Za-z.+-]*$ ]] || die "认不出版本号：$version"

  local missing
  missing=$(ldd "$root/feiyu-desktop" 2>/dev/null | awk '/not found/ {print $1}' | sort -u | tr '\n' ' ') || true

  local app="$BASE/app-$version"
  if [ -e "$app" ]; then
    mv -- "$app" "$tmp/old"
  fi
  mv -- "$root" "$app"
  touch -- "$app"

  write_shim "$BIN/feiyu-desktop" "$app/AppRun"
  local cli=''
  if [ ! -e "$BIN/feiyu" ] || grep -qF -- "$MARK" "$BIN/feiyu" 2>/dev/null; then
    write_shim "$BIN/feiyu" "$app/resources/backend/feiyu-backend"
    cli=yes
  else
    warn "$BIN/feiyu 是别的方式装的（比如源码版），没有覆盖；这一版的终端程序在 $app/resources/backend/feiyu-backend"
  fi

  local apps="$DATA/applications" icons="$DATA/icons/hicolor/256x256/apps"
  mkdir -p "$apps" "$icons"
  cp -f -- "$app/usr/share/icons/hicolor/256x256/apps/feiyu-desktop.png" "$icons/feiyu.png" 2>/dev/null \
    || warn '包里没找到图标，菜单里会是默认图标'
  local wmclass entry="$BIN/feiyu-desktop"
  wmclass=$(sed -n 's/^StartupWMClass=//p' "$app/feiyu.desktop" 2>/dev/null | head -n1) || true
  # 桌面文件的 Exec 有自己的转义规则，跟 shell 的不一样
  entry=${entry//\\/\\\\}; entry=${entry//\"/\\\"}; entry=${entry//\$/\\\$}; entry=${entry//\`/\\\`}; entry=${entry//%/%%}
  cat > "$apps/feiyu.desktop" <<DESKTOP_ENTRY
[Desktop Entry]
Type=Application
Name=Feiyu
Comment=AI coding workbench
Exec="$entry" %F
Icon=feiyu
Terminal=false
Categories=Development;
StartupWMClass=${wmclass:-feiyu}
DESKTOP_ENTRY
  if has update-desktop-database; then
    update-desktop-database "$apps" >/dev/null 2>&1 || true
  fi
  if [ -f "$DATA/icons/hicolor/icon-theme.cache" ] && has gtk-update-icon-cache; then
    gtk-update-icon-cache -q -t -f "$DATA/icons/hicolor" >/dev/null 2>&1 || true
  fi

  # 留下这一版和上一版：正开着的窗口可能还在用上一版的文件
  find "$BASE" -mindepth 1 -maxdepth 1 -type d -name 'app-*' -printf '%T@\t%p\n' \
    | sort -rn | tail -n +3 | cut -f2- \
    | while IFS= read -r old; do rm -rf -- "$old"; done

  case ":$PATH:" in
    *":$BIN:"*) ;;
    *) warn "$BIN 不在 PATH 里：可以直接运行 $BIN/feiyu，或者把 export PATH=\"\$HOME/.local/bin:\$PATH\" 加进 shell 配置" ;;
  esac
  say "装好了 Feiyu $version"
  say '桌面版：应用菜单里的 Feiyu，或者运行 feiyu-desktop'
  if [ -n "$cli" ]; then
    say '终端版：运行 feiyu'
  fi
  say '第一次打开会请你「连接模型」：选个接口、贴上 key 就能用'
  if [ -n "$missing" ]; then
    warn "桌面版还缺这些系统库：$missing（装上才开得了窗口，终端版不受影响）"
  fi
}

# 整个脚本都在上面的函数里，到这一行才开始做事：curl | bash 是边下边执行的，
# 下到一半断了的话 bash 读不到这一行，什么都不会发生。
main "$@"
