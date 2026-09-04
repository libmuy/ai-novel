#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则切片生成 (build_rule_slices.py)

`00_系统架构规范.md` §二·A 允许一个事实有第二份呈现，但只准三种形态之一：
不重复 / **脚本派生的只读视图** / 被 audit 交叉校验。

`00_通用写作规则_生成版.md` 与 `_校验版.md` 长期是第四种：**人工维护的有损压缩**。
它们的文件头自己就写着「人工维护派生切片」。后果是可预期的——校验版把
「登记到伏笔**登记表**」抄成了「登记到伏笔**跟踪册**」，指向了下游而非权威。

本脚本把它们改成真正的脚本派生：按权威版各章标题下的 `<!-- slice: 名称 -->`
标记，**逐字**切出对应章节。

**切片只做「取哪几章」，不做改写压缩。** 手工压缩正是漂移的来源；这与
`00_云端提示词生成器.md` 对提示词的要求（规则件全文内联、不做本章适用部分节录）
是同一条道理。代价是切片比手工压缩版大几 KB，换的是「永不漂移」。

用法
----
    build_rule_slices.py [仓库根] [--write] [--list]

    默认只**检查**：重出结果与磁盘上的切片逐字比对，不一致则打印差异摘要并返回 1。
    --write   按标记重新生成并写盘
    --list    只列出「哪些章进哪个切片」，不读写切片文件

配置在脚本内 `SLICES`：切片文件名、用途说明。要新增切片，加一条配置 +
在权威版对应章标题下加 `<!-- slice: 新名称 -->`。
`audit_rules.py` 的 `RULE008` 会在 check.sh 里跑同一套比对。
"""
import argparse
import difflib
import re
import sys
from pathlib import Path

AUTHORITY = "00_通用模板/01_写作规则/00_通用写作规则.md"

# 切片名 → (输出路径, 一句话用途)
SLICES = {
    "生成版": ("00_通用模板/01_写作规则/00_通用写作规则_生成版.md",
             "正文生成阶段前置指导（屏蔽后置自检清单，降低 Prompt Token）"),
    "校验版": ("00_通用模板/01_写作规则/00_通用写作规则_校验版.md",
             "正文生成后的自检与审校"),
}

_MARKER_RE = re.compile(r"^\s*<!--\s*slice:\s*(.+?)\s*-->\s*$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")


class SliceError(Exception):
    pass


def parse_sections(text: str) -> list[tuple[str, list[str], list[str]]]:
    """权威版 → [(章标题, 该章所属切片名列表, 该章正文行（含标题、不含标记行）)]。

    章 = 二级标题（`## `）到下一个二级标题之间。标记行紧跟在标题之后，
    不进入切片正文——切片里不该看见拼装用的记号。
    """
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if _H2_RE.match(l)]
    out = []
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        title = _H2_RE.match(lines[i]).group(1)
        body, names = [lines[i]], []
        for l in lines[i + 1:end]:
            m = _MARKER_RE.match(l)
            if m:
                names += [x.strip() for x in re.split(r"[,，、]", m.group(1)) if x.strip()]
            else:
                body.append(l)
        # 去掉章末尾多余空行与分隔线，渲染时由生成器统一补
        while body and (not body[-1].strip() or body[-1].strip() == "---"):
            body.pop()
        out.append((title, names, body))
    return out


def render_slice(name: str, sections: list, authority_rel: str, title_line: str) -> str:
    if name not in SLICES:
        raise SliceError(f"未配置的切片名「{name}」；已配置的是 {sorted(SLICES)}——"
                         f"要新增切片，先在 SLICES 里加一条")
    _path, purpose = SLICES[name]
    picked = [(t, b) for t, ns, b in sections if name in ns]
    if not picked:
        raise SliceError(f"切片「{name}」没有匹配到任何章节——检查权威版里的 "
                         f"`<!-- slice: {name} -->` 标记是否写对")
    head = [
        f"# {title_line}（{name}）",
        "",
        "> **派生 · 禁止手工编辑 · 非权威**",
        f"> 由 `02_工具/00_系统级/build_rule_slices.py --write` 从 `{authority_rel}`"
        f" **逐字**切出。",
        f"> 用途：{purpose}。",
        ">",
        "> **要改规则请改权威版，然后重跑本脚本**；直接改本文件会被 `audit_rules.py`"
        " 的 `RULE008` 拦下。",
        "> 切片只做「取哪几章」的选择，不做改写压缩——章节号沿用权威版，故可能跳号。",
        ">",
        f"> **收录章节**：{' / '.join(t for t, _ in picked)}",
        "",
        "---",
        "",
    ]
    body: list[str] = []
    for i, (_t, b) in enumerate(picked):
        if i:
            body += ["", "---", ""]
        body += b
    return "\n".join(head + body).rstrip() + "\n"


def build(repo_root: Path) -> dict[str, str]:
    src = repo_root / AUTHORITY
    if not src.exists():
        raise SliceError(f"权威版不存在：{AUTHORITY}")
    text = src.read_text(encoding="utf-8")
    first = next((l for l in text.splitlines() if l.startswith("# ")), "# 通用写作规则")
    sections = parse_sections(text)

    unmarked = [t for t, ns, _ in sections if not ns]
    if unmarked:
        print(f"提示：{len(unmarked)} 个章节没有 slice 标记，不进任何切片："
              f"{'、'.join(unmarked)}", file=sys.stderr)

    known = set(SLICES)
    for t, ns, _ in sections:
        for n in ns:
            if n not in known:
                raise SliceError(f"章节「{t}」标了未知切片名「{n}」；已配置的是 {sorted(known)}")

    return {n: render_slice(n, sections, AUTHORITY, first[2:].strip()) for n in SLICES}


def main() -> int:
    ap = argparse.ArgumentParser(description="按 slice 标记从权威版逐字切出规则切片")
    ap.add_argument("repo_root", nargs="?", default=None)
    ap.add_argument("--write", action="store_true", help="重新生成并写盘")
    ap.add_argument("--list", action="store_true", help="只列出章节→切片映射")
    args = ap.parse_args()

    repo_root = (Path(args.repo_root).resolve() if args.repo_root
                 else Path(__file__).resolve().parents[2])

    try:
        if args.list:
            text = (repo_root / AUTHORITY).read_text(encoding="utf-8")
            for t, ns, b in parse_sections(text):
                print(f"  {'、'.join(ns) or '—':<12} {t}  ({len(b)} 行)")
            return 0
        rendered = build(repo_root)
    except SliceError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2

    stale = []
    for name, content in rendered.items():
        out = repo_root / SLICES[name][0]
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current == content:
            print(f"  ✔ {SLICES[name][0]}（与权威版一致，{len(content.encode())//1024} KB）")
            continue
        stale.append(name)
        if args.write:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
            print(f"  ↻ {SLICES[name][0]} 已重出（{len(content.encode())//1024} KB）")
        else:
            diff = list(difflib.unified_diff(
                current.splitlines(), content.splitlines(),
                "磁盘上的切片", "按权威版重出", lineterm="", n=0))
            print(f"  ✘ {SLICES[name][0]} 与权威版不一致（{len(diff)} 行差异）")
            for l in diff[:12]:
                print(f"      {l}")
            if len(diff) > 12:
                print(f"      …（另有 {len(diff) - 12} 行）")

    if stale and not args.write:
        print("\n跑 `build_rule_slices.py --write` 重出；"
              "要改规则请改权威版，不要直接改切片。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
