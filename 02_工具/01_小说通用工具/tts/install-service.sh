#!/usr/bin/env bash
# 把 serve_audio.py 装成 systemd 常驻服务（开机自启、崩溃重拉）。
#
#   sudo bash 02_工具/01_小说通用工具/tts/install-service.sh [选项]
#
# 选项：
#   --novel-dir PATH  要服务的小说目录，默认 <repo>/01_小说数据/00_苍玄
#   --port N          默认 8765
#   --host ADDR       默认 0.0.0.0（同局域网可访问；无鉴权）
#   --user NAME       跑服务的用户，默认 $SUDO_USER，否则当前用户
#   --name SUFFIX     装成 serve-audio-<SUFFIX>.service（多本小说各占一个端口）
#   --uninstall       停用并删除对应 unit
#
# 卸载：sudo bash …/install-service.sh --uninstall [--name SUFFIX]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." && pwd)"
TEMPLATE="$SCRIPT_DIR/serve-audio.service.in"

NOVEL_DIR="$REPO/01_小说数据/00_苍玄"
PORT=8765
HOST=0.0.0.0
SVC_USER="${SUDO_USER:-$(id -un)}"
NAME=""
UNINSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --novel-dir) NOVEL_DIR="$2"; shift 2;;
    --port)      PORT="$2"; shift 2;;
    --host)      HOST="$2"; shift 2;;
    --user)      SVC_USER="$2"; shift 2;;
    --name)      NAME="$2"; shift 2;;
    --uninstall) UNINSTALL=1; shift;;
    *) echo "未知参数: $1" >&2; exit 2;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "需要 root：sudo bash $0 ..." >&2; exit 1; }

UNIT="serve-audio${NAME:+-$NAME}.service"
DEST="/etc/systemd/system/$UNIT"

if [ "$UNINSTALL" -eq 1 ]; then
  systemctl disable --now "$UNIT" 2>/dev/null || true
  rm -f "$DEST"
  systemctl daemon-reload
  echo "已卸载 $UNIT"
  exit 0
fi

[ -f "$TEMPLATE" ] || { echo "模板不存在: $TEMPLATE" >&2; exit 1; }
[ -d "$NOVEL_DIR/10_正文" ] || { echo "小说目录不像样（缺 10_正文/）: $NOVEL_DIR" >&2; exit 1; }
id "$SVC_USER" >/dev/null 2>&1 || { echo "用户不存在: $SVC_USER" >&2; exit 1; }
TITLE="$(basename "$NOVEL_DIR" | sed 's/^[0-9]*_//')"

sed -e "s|@@REPO@@|$REPO|g" \
    -e "s|@@NOVEL_DIR@@|$NOVEL_DIR|g" \
    -e "s|@@USER@@|$SVC_USER|g" \
    -e "s|@@HOST@@|$HOST|g" \
    -e "s|@@PORT@@|$PORT|g" \
    -e "s|@@TITLE@@|$TITLE|g" \
    "$TEMPLATE" > "$DEST"
chmod 644 "$DEST"

systemctl daemon-reload
systemctl enable --now "$UNIT"
sleep 1

echo "==================================================="
systemctl --no-pager --full status "$UNIT" | sed -n '1,10p' || true
echo "==================================================="
IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++)if($i=="src"){print $(i+1);exit}}')"
echo "unit   : $UNIT  ($DEST)"
echo "播放页 : http://${IP:-<pi-ip>}:$PORT/"
echo "播客RSS: http://${IP:-<pi-ip>}:$PORT/feed.xml"
echo "日志   : journalctl -u $UNIT -f"
echo "重启   : sudo systemctl restart $UNIT"
