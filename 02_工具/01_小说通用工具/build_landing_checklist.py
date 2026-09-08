#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
细纲落地核对清单生成器 (build_landing_checklist.py)

冷读找「写错的 / 写多的」，这一步找「该写没写的」——云端模型在零上下文里重写正文，
最爱把动机、前因、"这是老习惯"这类连接性交代压掉，只留鲜活的动作，冷读未必抓得到。
技能 `04_单章质量验收.md` 步骤 3.5「细纲落地核对」原本纯手工，弱 Agent 容易整步跳过；
本工具把它降级成填空题：细纲里每条 beat / 资源 / 伏笔 / 钩子 / 道义 / 突破代价都摆成
一行待锚定项，操作者只需指出正文落地句或标 `❌未落地`。

用法：
    python3 02_工具/01_小说通用工具/build_landing_checklist.py <章目录> [--force]

产出 `<章目录>/02_状态/03_细纲落地核对.md`。填完这张表是正文转「定稿」的前置门禁
（`audit_consistency.py` 规则 `PROGRESS005`）。
"""
import argparse
import datetime
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "00_系统级"))

import state_tree as st  # noqa: E402
from prompt_build.extract import (  # noqa: E402
    scene_blocks, read_section, table_rows, field_value,
)

CHECKLIST_FILENAME = "03_细纲落地核对.md"
_PLACEHOLDER = "〔待填：正文「≤12字」／❌未落地（原因）〕"
_ROW_KEYS = ("场景钩子", "涉及资源", "涉及伏笔")
_EMPTY = ("", "无", "—", "-", "无。")


def _chapter_number(name):
    m = re.search(r"章0*(\d+)", name or "")
    return m.group(1).zfill(4) if m else "0000"


def _resolve_chapter_dir(chapter_dir):
    """接受完整路径，也接受相对小说根的短路径（`05_工作区/...`）——
    后者在 `01_小说数据/*/` 下逐本试。"""
    if os.path.isdir(chapter_dir):
        return os.path.abspath(chapter_dir)
    norm = chapter_dir.replace("\\", "/")
    if "05_工作区" in norm:
        tail = norm[norm.index("05_工作区"):]
        import glob
        repo_root = os.path.abspath(os.path.join(_HERE, "..", ".."))
        for base in sorted(glob.glob(os.path.join(repo_root, "01_小说数据", "*"))):
            cand = os.path.join(base, tail)
            if os.path.isdir(cand):
                return os.path.abspath(cand)
    return os.path.abspath(chapter_dir)


def _bullets(block_text):
    """`**场景要点**` 之后的顶层 `- ` bullet 列表（次级缩进行并进上一条）。"""
    out, started = [], False
    for line in block_text.splitlines():
        s = line.strip()
        if s.startswith("**场景要点**") or s.startswith("**要点**"):
            started = True
            continue
        if not started:
            continue
        if re.match(r"^#{1,6}\s", s) or (s.startswith("**") and s.endswith("**")):
            break
        m = re.match(r"^[-*]\s+(.*)$", s)
        if m:
            out.append(m.group(1).strip())
        elif s and out and not s.startswith("|"):
            out[-1] += " " + s
    return out


def _items_from_outline(outline):
    """→ [(分组, 条目文本), ...]，按细纲出现顺序。"""
    items = []
    for title, body in scene_blocks(outline):
        scene = re.split(r"[·・\s]", title.strip(), maxsplit=1)[0]  # 「第1场景」
        for cells in table_rows(body):
            if len(cells) >= 2 and cells[0] in _ROW_KEYS and cells[1] not in _EMPTY:
                items.append((scene, f"{cells[0]}：{cells[1]}"))
        for b in _bullets(body):
            items.append((scene, f"要点：{b}"))

    hooks = read_section(outline, "【章级钩子】")
    for k in ("章末钩子内容", "与下章衔接点"):
        v = field_value(hooks, k)
        if v not in _EMPTY:
            items.append(("章级钩子", f"{k}：{v}"))

    dao = field_value(read_section(outline, "【道义与感悟】"), "本章落地道义")
    if dao not in _EMPTY:
        items.append(("道义与感悟", dao))

    bp = read_section(outline, "【本章突破卡】")
    if bp.strip():
        for k in ("突破类型", "即时代价"):
            v = field_value(bp, k)
            if v not in _EMPTY:
                items.append(("本章突破卡", f"{k}：{v}"))
    return items


def build_landing_checklist(chapter_dir, novel_dir=None, *, force=False,
                            grandfather=None, verbose=True):
    chapter_dir = _resolve_chapter_dir(chapter_dir)
    if os.path.basename(chapter_dir) == "02_状态":
        state_dir = chapter_dir
    elif os.path.isdir(os.path.join(chapter_dir, "02_状态")):
        state_dir = os.path.join(chapter_dir, "02_状态")
    else:
        state_dir = chapter_dir
    os.makedirs(state_dir, exist_ok=True)
    target = os.path.join(state_dir, CHECKLIST_FILENAME)
    if os.path.isfile(target) and os.path.getsize(target) > 0 and not force:
        print(f"错误: {target} 已存在且非空。要重建用 --force（会丢掉已填锚点）。")
        sys.exit(1)

    if not novel_dir:
        novel_dir = st.find_novel_dir(state_dir)
    if not novel_dir:
        print("错误: 无法定位小说根目录")
        sys.exit(1)
    novel_dir = os.path.abspath(novel_dir)

    plan_path = st.plan_path_for_chapter(state_dir, novel_dir)
    if not plan_path or not os.path.isfile(plan_path):
        print(f"错误: 找不到本章细纲（{plan_path}）——先落位细纲再生成清单。")
        sys.exit(1)
    outline = open(plan_path, encoding="utf-8", errors="ignore").read()
    items = _items_from_outline(outline)
    if not items:
        print(f"错误: 细纲里没抽到任何可核对项（场景表/要点/钩子/道义/突破卡都空）：{plan_path}")
        sys.exit(1)

    cnum = _chapter_number(os.path.basename(plan_path))
    today = datetime.date.today().isoformat()

    lines = [
        f"# 章{cnum} 细纲落地核对",
        "",
        f"> 生成时间：{today}　生成器：`build_landing_checklist.py`",
        "> 冷读找「写错的/写多的」，这一步找「该写没写的」。",
        "> **逐条**在正文里指出落地锚点句（引 ≤12 字）或标 `❌未落地（原因）`；",
        "> 未清项（未勾选的复选框 / 残留占位符 / 未 waive 的未落地项）会挡住正文转「定稿」（`PROGRESS005`）。",
        "> 确属本章不必落地的：改成 `- [x]` 并把锚点行写成 `豁免：<理由>`。",
        "",
    ]
    if grandfather:
        lines += [f"> **回溯豁免**：{grandfather}——本表由机制上线后补建、未逐条回溯，"
                  f"全部条目按豁免处理。", ""]
    group = None
    for g, text in items:
        if g != group:
            lines += ["", f"## {g}", ""]
            group = g
        one = re.sub(r"\s+", " ", text).strip()
        if grandfather:
            lines.append(f"- [x] {one}")
            lines.append(f"      → 锚点：豁免：{grandfather}")
        else:
            lines.append(f"- [ ] {one}")
            lines.append(f"      → 锚点：{_PLACEHOLDER}")
    payload = "\n".join(lines) + "\n"
    st._atomic_write(target, payload)
    if verbose:
        print(f"已写入落地核对清单: {target}（{len(items)} 条待锚定）")
    return target


def main():
    ap = argparse.ArgumentParser(description="从单章细纲生成「细纲落地核对」清单")
    ap.add_argument("chapter_dir",
                    help="章工作区目录，如 05_工作区/03_第01部/03_卷01/06_章0004")
    ap.add_argument("--novel-dir", help="小说根目录（缺省自动定位）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的非空清单")
    ap.add_argument("--grandfather", metavar="理由",
                    help="回溯豁免：所有条目按已豁免生成（仅用于机制上线前已定稿的旧章补建）")
    args = ap.parse_args()
    build_landing_checklist(args.chapter_dir, args.novel_dir,
                            force=args.force, grandfather=args.grandfather)


if __name__ == "__main__":
    main()
