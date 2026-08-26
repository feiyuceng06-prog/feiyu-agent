#!/usr/bin/env bash
# 一键安装：建虚拟环境、装依赖、往 PATH 上放个 shim
# 重复运行是安全的，会就地更新
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$PWD"
BIN="${HOME}/.local/bin"
SHIM="${BIN}/feiyu"

say()  { printf '\033[38;2;150;103;74m▸\033[0m %s\n' "$1"; }
warn() { printf '\033[38;2;168;132;60m!\033[0m %s\n' "$1"; }
die()  { printf '\033[38;2;176;87;78m✗\033[0m %s\n' "$1" >&2; exit 1; }

# ---- 找一个够新的 python ----
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done
[ -n "$PY" ] || die "需要 Python 3.10 以上，没找到 装一个再来：https://www.python.org/downloads/"
say "用 $($PY -V 2>&1)"

# ---- 虚拟环境 ----
if [ ! -x ".venv/bin/python" ]; then
  say "建虚拟环境 .venv"
  "$PY" -m venv .venv || die "建虚拟环境失败 Debian/Ubuntu 上可能要先装 python3-venv"
fi

say "装依赖（含 openai，用于 DeepSeek / Kimi / 各种中转站）"
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -e ".[openai]" || die "安装依赖失败"

# ---- PATH 上的 shim ----
mkdir -p "$BIN"
cat > "$SHIM" <<EOF
#!/usr/bin/env bash
exec "${ROOT}/.venv/bin/feiyu" "\$@"
EOF
chmod +x "$SHIM"
say "已放好 $SHIM"

# 真的能跑起来才算装好 只看 pip 退出码不够，缺文件是在 import 时才炸的
if ! VERSION="$(./.venv/bin/feiyu --version 2>&1)"; then
  echo
  printf '%s\n' "$VERSION" >&2
  die "装完了但跑不起来 把上面的报错发给我 "
fi
echo
say "装好了：$VERSION"

case ":${PATH}:" in
  *":${BIN}:"*) ;;
  *)
    echo
    warn "${BIN} 不在 PATH 里，直接敲 feiyu 会找不到 把下面这行加进 ~/.bashrc 或 ~/.zshrc："
    echo
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo
    warn "加完新开一个终端，或者先 source 一下配置文件 "
    ;;
esac

cat <<'EOF'

接下来两步：

    feiyu            进去之后打 /key add 添加你的第一个模型
                     （要填：接口地址、模型 id、API key）

    /help            看所有命令

EOF
