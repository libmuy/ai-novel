#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
基线候选生成器 (build_baseline.py)

扫描数据库卡片的「## 动态字段清单」小节，按已填写的「基线初值」生成
**基线候选状态树**，落到 `05_工作区/02_状态/00_基线候选/`（暂存区，不是基线本身）。

这是半自动化的第一步。生成后由 Agent / 用户按技能
`00_通用模板/03_任务技能/02_小说级/08_基线状态初始化.md` 的
【基线范围规则（硬规则）】筛选——只保留第 1 章开篇确有状态、且第 1 卷内
会登场/被主角博弈的对象——再提升为 `00_基线状态/`。

**纯标准库、无 LLM、无网络。绝不写 00_基线状态/ 或 01_最新状态/。**

用法:
    python3 02_工具/01_小说通用工具/build_baseline.py <小说目录> [--out DIR] [--format text|json]

判据:
    - 只登记「基线初值」列**非空**的字段（空 = 内容判断留给用户，不编）。
    - 覆盖有「## 动态字段清单」的卡片：人物（07_人物 + 主角档案）、势力（03_势力组织）。
      物品 / 财务 / 世界对象的基线仍按技能 08 手工整理。
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "00_系统级"))
import state_tree  # noqa: E402


PROTAGONIST_ID = "角色.苏砚"  # 01_设定/00_主角档案.md
PROTAGONIST_CARD = os.path.join("01_设定", "00_主角档案.md")

# (卡片目录, 文件名前缀, 对象ID前缀)
CARD_SOURCES = [
    (os.path.join("02_数据库", "07_人物"), "07_人物_", "角色"),
    (os.path.join("02_数据库", "03_势力组织"), "03_势力组织_", "势力"),
]

DEFAULT_OUT_SUBPATH = os.path.join("05_工作区", "02_状态", "00_基线候选")

_SECTION_RE = re.compile(r"^#{1,4}\s*动态字段清单\s*$")
_EMPTY_VALUES = {"", "-", "—", "待定", "待确认", "待用户确认", "N/A", "无"}


def parse_dynamic_fields(text):
    """从卡片正文提取「## 动态字段清单」表格，返回 [(字段, 类型, 基线初值), ...]。
    没有该小节返回 None；有小节但表格为空返回 []。"""
    lines = text.splitlines()
    in_section = False
    rows = []
    for line in lines:
        if _SECTION_RE.match(line.strip()):
            in_section = True
            continue
        if not in_section:
            continue
        s = line.strip()
        if s.startswith("#"):  # 下一个标题，小节结束
            break
        if not s.startswith("|"):
            continue
        parts = [p.strip() for p in s.split("|")[1:-1]]
        if len(parts) < 2:
            continue
        head = parts[0].replace("*", "")
        if head in ("字段", "字段名") or head.startswith(":-") or head.startswith("---"):
            continue
        field = head
        ftype = parts[1] if len(parts) > 1 else ""
        val = parts[2] if len(parts) > 2 else ""
        rows.append((field, ftype, val))
    return rows if in_section else None


def object_name_from_filename(fname, prefix):
    stem = fname[:-3] if fname.endswith(".md") else fname
    if not stem.startswith(prefix):
        return None
    return stem[len(prefix):] or None


def collect(novel_dir):
    """返回 (records, report)。"""
    records = []
    report = {
        "objects_with_values": [],   # [{"id":.., "fields":{f:v}}]
        "objects_all_blank": [],     # 有清单但初值全空
        "blank_fields": [],          # [{"id":.., "field":..}] 待用户确认
        "cards_without_section": [],  # 卡片路径
        "type_by_field": {},         # 字段 -> 类型（供报告核对）
    }

    def handle_card(rel_path, obj_id):
        abspath = os.path.join(novel_dir, rel_path)
        if not os.path.isfile(abspath):
            return
        with open(abspath, encoding="utf-8") as f:
            rows = parse_dynamic_fields(f.read())
        if rows is None:
            report["cards_without_section"].append(rel_path)
            return
        filled = {}
        for field, ftype, val in rows:
            report["type_by_field"].setdefault(field, ftype)
            v = val.strip()
            # 括号注解如「凡人（未入道）」保留；纯占位/空视为空
            if v in _EMPTY_VALUES or not v:
                report["blank_fields"].append({"id": obj_id, "field": field})
                continue
            filled[field] = (ftype, v)
        if filled:
            report["objects_with_values"].append(
                {"id": obj_id, "fields": {k: v[1] for k, v in filled.items()}}
            )
            for field, (ftype, v) in filled.items():
                records.append({
                    "object_id": obj_id, "field": field,
                    "type": ftype, "value": v, "meta": {},
                })
        else:
            report["objects_all_blank"].append(obj_id)

    handle_card(PROTAGONIST_CARD, PROTAGONIST_ID)

    for card_dir, prefix, id_prefix in CARD_SOURCES:
        abs_dir = os.path.join(novel_dir, card_dir)
        if not os.path.isdir(abs_dir):
            continue
        for fname in sorted(os.listdir(abs_dir)):
            if not fname.endswith(".md"):
                continue
            name = object_name_from_filename(fname, prefix)
            if name is None:            # 总索引 07_人物.md 等
                continue
            handle_card(os.path.join(card_dir, fname), f"{id_prefix}.{name}")

    return records, report


def render_text(report, records, out_dir):
    L = []
    L.append("=" * 60)
    L.append("基线候选生成报告")
    L.append("=" * 60)
    ow = report["objects_with_values"]
    L.append(f"\n【已有基线初值的对象：{len(ow)}】→ 已写入候选树")
    for o in ow:
        L.append(f"  {o['id']}")
        for f, v in o["fields"].items():
            L.append(f"      {f} = {v}")
    L.append(f"\n【有动态字段清单、但初值全空的对象：{len(report['objects_all_blank'])}】")
    for oid in report["objects_all_blank"]:
        L.append(f"  {oid}")
    bf = report["blank_fields"]
    L.append(f"\n【待用户确认的字段初值：{len(bf)} 处】（留空即「不要编」）")
    seen = {}
    for e in bf:
        seen.setdefault(e["id"], []).append(e["field"])
    for oid, fs in seen.items():
        L.append(f"  {oid}: {', '.join(fs)}")
    cw = report["cards_without_section"]
    if cw:
        L.append(f"\n【缺「## 动态字段清单」小节的卡片：{len(cw)}】")
        for p in cw:
            L.append(f"  {p}")
    L.append(f"\n候选树已写入: {out_dir}")
    L.append(f"记录数: {len(records)}  对象数: {len({r['object_id'] for r in records})}")
    L.append("\n下一步：按技能 08【基线范围规则】筛掉第 1 卷不登场的对象，")
    L.append("        补齐需要用户确认的初值，再提升为 00_基线状态/。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="扫描卡片动态字段清单 → 生成基线候选状态树")
    ap.add_argument("novel_dir", help="小说目录，如 01_小说数据/00_苍玄")
    ap.add_argument("--out", help=f"候选树输出目录（默认 <小说目录>/{DEFAULT_OUT_SUBPATH}）")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--no-write", action="store_true", help="只出报告，不写候选树")
    args = ap.parse_args()

    novel_dir = os.path.abspath(args.novel_dir)
    if not os.path.isdir(novel_dir):
        ap.error(f"目录不存在: {novel_dir}")

    out_dir = args.out or os.path.join(novel_dir, DEFAULT_OUT_SUBPATH)

    records, report = collect(novel_dir)

    if not args.no_write:
        os.makedirs(out_dir, exist_ok=True)
        state_tree.write_state_tree(
            out_dir, records,
            folded_chapter=None, tool="build_baseline.py",
            prune=True, manifest=False,
            note=("> 基线候选 · build_baseline.py 生成 · **不是基线本身**。\n"
                  "> 按技能 08【基线范围规则】筛选后再提升为 00_基线状态/。"),
        )
        readme = os.path.join(out_dir, "00_说明.md")
        state_tree._atomic_write(readme, (
            "# 基线候选（暂存区）\n\n"
            "> `build_baseline.py` 扫描卡片「## 动态字段清单」已填初值生成。\n"
            "> **这不是基线。** 需按技能 `00_通用模板/03_任务技能/02_小说级/08_基线状态初始化.md`\n"
            "> 的【基线范围规则】人工筛选（去掉第 1 卷不登场的对象）+ 补齐待确认初值，\n"
            "> 再写入 `../00_基线状态/`。筛选完成后本目录可删。\n"
        ))

    if args.format == "json":
        print(json.dumps({
            "out_dir": out_dir if not args.no_write else None,
            "record_count": len(records),
            "object_count": len({r["object_id"] for r in records}),
            "report": report,
        }, ensure_ascii=False, indent=2))
    else:
        print(render_text(report, records, out_dir if not args.no_write else "(未写)"))


if __name__ == "__main__":
    main()
