#!/usr/bin/env bash
# 章节配音 edge 后端依赖引导（幂等）。
# 在 02_工具/.venv 里装 edge-tts；tts_chapter.py 会自动发现 02_工具/.venv/bin/edge-tts。
#
#   bash 02_工具/01_小说通用工具/tts/setup.sh
#
# 系统无 pip 不要紧——用 venv + ensurepip 自带的 pip。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"          # 02_工具/
VENV="$TOOLS_DIR/.venv"

if [ ! -x "$VENV/bin/python" ]; then
  echo "==> 创建 venv: $VENV"
  python3 -m venv "$VENV"
fi

echo "==> ensurepip + 升级 pip"
"$VENV/bin/python" -m ensurepip --upgrade
"$VENV/bin/python" -m pip install --upgrade pip

echo "==> 安装 $SCRIPT_DIR/requirements.txt"
"$VENV/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"

echo "==> 校验"
"$VENV/bin/edge-tts" --list-voices >/dev/null && echo "edge-tts OK: $VENV/bin/edge-tts"
