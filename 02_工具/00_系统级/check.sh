#!/usr/bin/env bash
# 提交前的一键确定性检查（三关，全通过才算干净）：
#   ① 单元测试        —— 工具行为回归
#   ② 规则层审计      —— 00_通用模板/ 自身的一致性（audit_rules.py）
#   ③ 各小说数据审计  —— 01_小说数据/<每本书>（audit_consistency.py）
#
# 用法：
#   02_工具/00_系统级/check.sh              # 全跑，error 才失败
#   02_工具/00_系统级/check.sh --strict     # warning 也算失败
#   02_工具/00_系统级/check.sh --novel 00_苍玄   # 只审一本书（仍跑 ①②）
#
# 退出码 0 = 三关全过。CI / pre-commit 直接挂它即可。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

STRICT=""
ONLY_NOVEL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict) STRICT="--strict"; shift ;;
    --novel)  ONLY_NOVEL="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

# 优先用仓库自带 venv，没有就退回 python3
PY="$REPO_ROOT/02_工具/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

FAILED=()

run_step() {           # run_step <标题> <命令...>
  local title="$1"; shift
  printf '\n\033[1m━━ %s\033[0m\n' "$title"
  if "$@"; then
    printf '\033[32m   ✔ %s\033[0m\n' "$title"
  else
    printf '\033[31m   ✘ %s\033[0m\n' "$title"
    FAILED+=("$title")
  fi
}

run_step "① 单元测试" \
  "$PY" -m unittest discover -s "$SCRIPT_DIR/tests" -p 'test_*.py'

run_step "② 规则层审计（00_通用模板/）" \
  "$PY" "$SCRIPT_DIR/audit_rules.py" "$REPO_ROOT" --format text $STRICT

if [[ -n "$ONLY_NOVEL" ]]; then
  NOVELS=("01_小说数据/$ONLY_NOVEL")
else
  NOVELS=()
  for d in 01_小说数据/*/; do
    [[ -d "$d" ]] && NOVELS+=("${d%/}")
  done
fi

for novel in "${NOVELS[@]}"; do
  if [[ ! -d "$novel" ]]; then
    echo "跳过（目录不存在）：$novel" >&2
    continue
  fi
  run_step "③ 数据层审计（${novel##*/}）" \
    "$PY" 02_工具/01_小说通用工具/audit_consistency.py "$novel" --format text $STRICT
done

printf '\n══════════════════════════════════════\n'
if [[ ${#FAILED[@]} -eq 0 ]]; then
  printf '\033[32m全部通过。\033[0m\n'
  exit 0
fi
printf '\033[31m失败 %d 项：\033[0m\n' "${#FAILED[@]}"
printf '  - %s\n' "${FAILED[@]}"
exit 1
