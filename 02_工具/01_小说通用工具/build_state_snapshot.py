#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
只读状态快照工具 (build_state_snapshot.py)

从冻结基线 `05_工作区/02_状态/00_基线状态/` 折叠履历，产出只读的单张 4 列状态表。
**只读、永不调 LLM**：折叠范围内若有未合并的描述字段变更 -> 报错、退出码 2
（提示先对那些章跑 merge_chapter_state.py）。

三种模式
--------
    # 卷末快照：折叠到该卷最后一章（含），写 99_卷末状态快照.md
    python3 02_工具/01_小说通用工具/build_state_snapshot.py --volume-dir <卷目录> [--output PATH]

    # 某章开篇状态：折叠到该章之前（不含），默认打印 stdout
    #   —— 回溯改旧章时，滑动窗口任务需要「第 N 章开篇时的世界状态」
    python3 02_工具/01_小说通用工具/build_state_snapshot.py --at-chapter <章目录> [--output PATH]

    # 逐章开篇状态物化（W4.1）：为每章生成/刷新 00_开篇状态.md（值拷贝、派生视图）
    #   按各章单章细纲的「## 出场对象」小节裁剪；清单缺失则写全量并告警。
    #   merge_chapter_state.py / rebuild_global_state.py 写完最新状态后会自动调用本模式。
    python3 02_工具/01_小说通用工具/build_state_snapshot.py --write-chapter-openers <小说目录>

    # 履历骨架（A4）：从细纲「## 出场对象」+ 开篇状态 + 卡片动态字段清单，
    #   生成预填「对象/字段/类型/变更类型」的 01_状态履历.md，弱模型只需填「值」列、删无变化行。
    python3 02_工具/01_小说通用工具/build_state_snapshot.py --changelog-skeleton <章目录> [--force]
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
from state_lock import acquire_until_exit, StateLockError  # noqa: E402
from state_tree import render_md_table, StateMergeError, CHANGELOG_FILENAME  # noqa: E402


def _fold(novel_dir, changelog_paths):
    baseline = st.baseline_dir(novel_dir)
    if not os.path.isdir(baseline):
        print(f"错误: 冻结基线不存在: {baseline}")
        sys.exit(1)
    cache = st.load_merge_cache(novel_dir)
    try:
        records, _wb = st.fold_all(baseline, changelog_paths, cache=cache, resolver=None)
    except StateMergeError as e:
        print(f"\n[阻断] {e}\n先对涉及章运行 merge_chapter_state.py 合并描述字段后再生成快照。")
        sys.exit(2)
    return records


def write_chapter_openers(novel_dir, *, verbose=True):
    """为每章生成/刷新 00_开篇状态.md（派生视图）。
    第 N 章开篇状态 = 基线 ⊕ 折叠「排在第 N 章之前」的全部章履历，再按第 N 章
    单章细纲的「## 出场对象」清单裁剪。返回写入路径列表。

    覆盖两类章：
      ① 已建履历的章——折叠到该章履历之前；
      ② 「下一章」：工作区 `<章>/02_状态/` 目录已建、还没写 `01_状态履历.md` 的章
         （拼细纲提示词就要它的开篇状态）——折叠到全部已有履历。
    没有 ② 时，`--at-chapter <章目录> --output …/00_开篇状态.md` 也能单点物化，
    但那条路径写出的抬头是「开篇状态快照 · …」、非规范；本函数写的是规范抬头
    「# 本章开篇状态 · <章路径>」＋完整溯源注，两条路径产物应当一致。

    供 CLI（--write-chapter-openers）与 merge/rebuild 收尾自动调用。
    基线不存在时静默跳过（返回 []）。"""
    baseline = st.baseline_dir(novel_dir)
    if not os.path.isdir(baseline):
        if verbose:
            print("跳过逐章开篇状态刷新：冻结基线尚未初始化")
        return []
    changelogs = st.iter_workspace_changelogs(novel_dir)
    prot = st.protagonist_state_id(novel_dir)
    written = []

    def _emit(chap_dir, chap_name, records):
        cast = st.parse_chapter_cast(st.plan_path_for_chapter(chap_dir, novel_dir), prot)
        if cast is None:
            shown, missing = records, None
        else:
            present = {r["object_id"] for r in records}
            shown = [r for r in records if st.cast_contains(cast, r["object_id"])]
            missing = cast - present
        out = os.path.join(chap_dir, st.CHAPTER_OPENER_FILENAME)
        st._atomic_write(out, st.render_chapter_opener(shown, chap_name, cast, missing))
        written.append(out)
        if verbose:
            tag = "全量(无出场对象清单)" if cast is None else f"{len(shown)}/{len(records)} 对象"
            print(f"  开篇状态: {chap_name}  [{tag}]")

    for i, cl in enumerate(changelogs):
        _emit(os.path.dirname(cl), st.chapter_rel_name(cl, novel_dir),
              _fold(novel_dir, changelogs[:i]))   # 严格早于本章的全部章

    # ② 下一章（目录已建、无履历）
    done = {os.path.normpath(os.path.dirname(cl)) for cl in changelogs}
    ws = os.path.join(novel_dir, st.WORKSPACE_DIRNAME)
    for dirpath, _dirs, _files in os.walk(ws):
        if os.path.basename(dirpath) != "02_状态":
            continue
        state_dir = dirpath
        synth_cl = os.path.normpath(os.path.join(state_dir, st.CHANGELOG_FILENAME))
        chap_dir = os.path.normpath(state_dir)  # render 目标是 02_状态/ 目录
        if os.path.normpath(state_dir) in done:
            continue
        try:
            key = st.chapter_sort_key(synth_cl, novel_dir)
        except Exception:
            continue  # 不符合章目录规范——跳过，不静默乱排
        earlier = [cl for cl in changelogs
                   if st.chapter_sort_key(cl, novel_dir) < key]
        _emit(chap_dir, st.chapter_rel_name(synth_cl, novel_dir),
              _fold(novel_dir, earlier))
    return written


# ─────────────────────────────────────────────────────────────────────
# 履历骨架生成器（A4）：从细纲「## 出场对象」+ 开篇状态 + 卡片动态字段清单，
# 生成预填好「对象/字段/类型/变更类型」的 01_状态履历.md 骨架，弱模型只需填「值」列。
# ─────────────────────────────────────────────────────────────────────

_PLACEHOLDER = "〔待填；本章无变化则删除本行〕"

# 出场方式前缀 → 是否要出变更行
_MODE_EMIT_PREFIXES = ("登场", "新建", "状态变动", "张力驱动")
_MODE_SKIP_PREFIXES = ("提及", "在册", "施压")

# @引用类型 → 状态对象前缀（parse_cast 的 Ref.ref_type）
_REF_TYPE_TO_STATE_PREFIX = {
    "人物": "角色", "势力": "势力", "物品": "物品",
    "财务": "财务", "世界": "世界",
}
_NON_STATE_REF_TYPES = {"伏笔", "道义", "区域", "地名", "书籍", "资源", "类型"}

# 各类别新对象兜底字段集（无动态字段清单时用；与 03_字段词表.md §二 对齐）
_FALLBACK_FIELDS = {
    "角色": [("境界", "运算-枚举"), ("身体状况", "描述"), ("内力值", "运算-数值"),
             ("所在地", "描述"), ("外貌变化", "描述"), ("当前心境", "描述"),
             ("隐患·未解代价", "描述"), ("对象终态", "运算-枚举")],
    "势力": [("综合实力", "描述"), ("领地·据点", "描述"), ("隶属", "运算-枚举"),
             ("与主角互动状态", "运算-枚举"), ("当前动向", "描述")],
    "物品": [("持有者", "运算-枚举"), ("物品状态", "运算-枚举"), ("数量·耐久", "运算-数值"),
             ("等级·类别", "运算-枚举"), ("附加说明", "描述"),
             ("物理状态", "描述"), ("神魂链接", "描述")],
    "财务": [("财富分级", "运算-枚举"), ("灵石结余", "运算-数值"),
             ("主要资产", "运算-列表"), ("人情债·债务", "运算-列表")],
    "关系": [("关系性质", "运算-枚举"), ("亲疏", "运算-枚举"), ("公开程度", "运算-枚举"),
             ("关系概述", "描述"), ("甲对乙态度", "描述"), ("乙对甲态度", "描述")],
    "世界": [("世界状态", "描述"), ("揭示进度", "描述"), ("异常现象", "描述")],
}


def _chapter_number(chap_rel):
    m = re.search(r"章(\d{3,4})", chap_rel)
    return m.group(1) if m else "0000"


def _resolve_chapter_dir(chapter_dir):
    """接受完整路径，也接受相对小说根的短路径（`05_工作区/...`）——
    后者在 `01_小说数据/*/` 下逐本试。"""
    if os.path.isdir(chapter_dir):
        return os.path.abspath(chapter_dir)
    norm = chapter_dir.replace("\\", "/")
    if "05_工作区" in norm:
        import glob
        tail = norm[norm.index("05_工作区"):]
        repo_root = os.path.abspath(os.path.join(_HERE, "..", ".."))
        for base in sorted(glob.glob(os.path.join(repo_root, "01_小说数据", "*"))):
            cand = os.path.join(base, tail)
            if os.path.isdir(cand):
                return os.path.abspath(cand)
    return os.path.abspath(chapter_dir)


def _dynamic_fields_for(novel_dir, prefix, name):
    """新 角色/势力 对象的字段清单：优先卡片「## 动态字段清单」，回退兜底集。返回 [(field, type), ...]。"""
    try:
        from build_baseline import parse_dynamic_fields
    except Exception:
        parse_dynamic_fields = None
    cands = []
    if prefix == "角色":
        cands = [os.path.join(novel_dir, "02_数据库", "07_人物", f"07_人物_{name}.md"),
                 os.path.join(novel_dir, "01_设定", "00_主角档案.md")]
    elif prefix == "势力":
        cands = [os.path.join(novel_dir, "02_数据库", "03_势力组织", f"03_势力组织_{name}.md")]
    if parse_dynamic_fields:
        for c in cands:
            if not os.path.isfile(c):
                continue
            rows = parse_dynamic_fields(open(c, encoding="utf-8", errors="ignore").read())
            if rows:
                return [(f, t or "描述") for f, t, _v in rows], os.path.relpath(c, novel_dir)
    return _FALLBACK_FIELDS.get(prefix, []), None


def _resolve_cast_id(novel_dir, ref):
    """Ref → (状态对象ID | None, 说明)。"""
    if ref.ref_type == "主角":
        return st.protagonist_state_id(novel_dir), None
    if ref.ref_type == "关系":
        try:
            return st.normalize_relation_id("关系." + ref.name), None
        except st.RelationIdError as e:
            return None, f"关系ID 不合规（{e}）——请手动核对"
    if ref.ref_type in _NON_STATE_REF_TYPES:
        return None, f"@{ref.ref_type} 非状态对象，不进履历"
    pref = _REF_TYPE_TO_STATE_PREFIX.get(ref.ref_type)
    if not pref:
        return None, f"未知引用类型 @{ref.ref_type}"
    return f"{pref}.{ref.name}", None


def build_changelog_skeleton(chapter_dir, novel_dir=None, *, force=False, verbose=True):
    """为一章生成预填的 01_状态履历.md 骨架。返回写入路径。"""
    from prompt_build.extract import parse_cast

    chapter_dir = _resolve_chapter_dir(chapter_dir)
    if os.path.basename(chapter_dir) == "02_状态":
        state_dir = chapter_dir
    elif os.path.isdir(os.path.join(chapter_dir, "02_状态")):
        state_dir = os.path.join(chapter_dir, "02_状态")
    else:
        state_dir = chapter_dir
    os.makedirs(state_dir, exist_ok=True)
    target = os.path.join(state_dir, CHANGELOG_FILENAME)
    if os.path.isfile(target) and os.path.getsize(target) > 0 and not force:
        print(f"错误: {target} 已存在且非空。要覆盖用 --force（会丢掉已填内容）。")
        sys.exit(1)

    if not novel_dir:
        novel_dir = st.find_novel_dir(state_dir)
    if not novel_dir:
        print("错误: 无法定位小说根目录")
        sys.exit(1)
    novel_dir = os.path.abspath(novel_dir)

    plan_path = st.plan_path_for_chapter(state_dir, novel_dir)
    if not plan_path or not os.path.isfile(plan_path):
        print(f"错误: 找不到本章细纲（{plan_path}）——先落位细纲再生成骨架。")
        sys.exit(1)
    outline = open(plan_path, encoding="utf-8", errors="ignore").read()
    cast = parse_cast(outline)
    if not cast:
        print(f"错误: 细纲缺「## 出场对象」表或表为空：{plan_path}")
        sys.exit(1)

    # 折叠严格早于本章的全部履历 → 每对象已有字段
    changelogs = st.iter_workspace_changelogs(novel_dir)
    synth_cl = os.path.normpath(os.path.join(state_dir, CHANGELOG_FILENAME))
    try:
        this_key = st.chapter_sort_key(synth_cl, novel_dir)
        earlier = [c for c in changelogs if st.chapter_sort_key(c, novel_dir) < this_key]
    except Exception:
        earlier = [c for c in changelogs if c < synth_cl]
    records = _fold(novel_dir, earlier)
    cur = {}
    for r in records:
        cur.setdefault(r["object_id"], []).append((r["field"], r["type"], r["value"]))

    chap_rel = st.chapter_rel_name(synth_cl, novel_dir)
    cnum = _chapter_number(chap_rel)
    today = datetime.date.today().isoformat()
    prot = st.protagonist_state_id(novel_dir)

    # 突破卡提示（如有）
    from prompt_build.extract import read_section
    bp_sec = read_section(outline, "【本章突破卡】")
    bp_hint = None
    if bp_sec.strip():
        m = re.search(r"突破\s*\|\s*([^\n|]+)", bp_sec)
        bp_hint = (m.group(1).strip() if m else "见细纲【本章突破卡】")

    lines = [f"| 对象ID | 字段 | 类型 | 值 | 章节号 | 变更时间 | 变更类型 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    comments = []  # 表前的分类注释
    seen_ids = set()
    for entry in cast:
        oid, note = _resolve_cast_id(novel_dir, entry.ref)
        mode = (entry.mode or "").strip()
        label = f"@{entry.ref.ref_type}" + (f".[{entry.ref.name}]" if entry.ref.name else "")
        if oid is None:
            comments.append(f"<!-- {label} 出场方式={mode or '—'}：{note} -->")
            continue
        if oid in seen_ids:
            continue
        seen_ids.add(oid)
        emit = mode.startswith(_MODE_EMIT_PREFIXES) or mode == ""
        skip = mode.startswith(_MODE_SKIP_PREFIXES)
        if skip and not emit:
            comments.append(
                f"<!-- {label}（{oid}）出场方式={mode}：本章通常无状态变化；确有请手动加行 -->")
            continue
        unknown = not emit and not skip
        lines.append("")
        lines.append(f"<!-- ── {oid}  （{label}，出场方式={mode or '—'}）"
                     + (f"  细纲备注：{entry.note}" if entry.note else "") + " -->")
        if unknown:
            lines.append(f"<!-- ⚠ 未识别的出场方式「{mode}」，人工确认这个对象本章是否真有状态变化 -->")
        prefix = oid.split(".", 1)[0]
        if oid in cur:  # 已有对象 → 逐已有字段出「修改」行
            have = set()
            for field, ftype, oldval in cur[oid]:
                have.add(field)
                lines.append(f"<!-- 上章值：{oldval[:80]} -->")
                extra = ""
                if oid == prot and field == "境界" and bp_hint:
                    extra = (f"  <!-- 突破卡：{bp_hint}；即时代价落「隐患·未解代价」，"
                             f"别只塞「身体状况」 -->")
                lines.append(f"| {oid} | {field} | {ftype} | {_PLACEHOLDER} | {cnum} | {today} | 修改 |{extra}")
            # 本类别里本对象尚无的字段——本章若首次出现（如异宝首次建立神魂链接）才填
            extra_fields = [(f, t) for f, t in _FALLBACK_FIELDS.get(prefix, []) if f not in have]
            if extra_fields:
                lines.append(f"<!-- 下面是 {prefix}类还没记过的字段：本章首次出现才填、否则删 -->")
                for field, ftype in extra_fields:
                    lines.append(f"| {oid} | {field} | {ftype} | {_PLACEHOLDER} | {cnum} | {today} | 新建 |")
        else:  # 新对象
            if prefix in ("角色", "势力"):
                fields, src = _dynamic_fields_for(novel_dir, prefix, entry.ref.name)
                if src:
                    lines.append(f"<!-- 字段/初值参考：{src}【动态字段清单】 -->")
                else:
                    lines.append(f"<!-- {prefix}卡无「## 动态字段清单」，下面是兜底字段集，按需增删 -->")
            else:
                fields = _FALLBACK_FIELDS.get(prefix, [])
                if prefix == "关系":
                    lines.append("<!-- 关系性质/亲疏/公开程度 是必填闭集；一端死亡/退场再补 对象终态=终结 -->")
            for field, ftype in fields:
                lines.append(f"| {oid} | {field} | {ftype} | {_PLACEHOLDER} | {cnum} | {today} | 新建 |")

    # footer：一次性消耗品等「不进状态」的变化
    cast_sec = read_section(outline, "出场对象")
    consumables = [l.strip().lstrip(">").strip()
                   for l in cast_sec.splitlines()
                   if l.strip().startswith(">") and ("不入" in l or "不进状态" in l)]
    foot = ["", "> **本章已在正文体现、无需进状态的变化**（一次性消耗品/道具/群像，不建状态对象）："]
    for c in consumables:
        foot.append(f"> - 〔{c}〕")
    foot.append("> - 〔按需补充〕")

    header = [
        f"# 章{cnum} 状态履历",
        "",
        f"> 章节号：{cnum}",
        f"> 变更时间：{today}",
        f"> 本章主要事件：〔待填〕",
        ">",
        "> 骨架由 `build_state_snapshot.py --changelog-skeleton` 生成：",
        "> 「对象/字段/类型/变更类型」已排好，**只需填「值」列、删掉本章无变化的整行**。",
        "> `〔待填…〕` 占位符必须替换或删行——`merge_chapter_state.py` 折叠前会拦残留占位符。",
        "> `<!-- -->` 注释是提示，可留可删。",
        "",
        "## 状态变更",
        "",
    ]
    if comments:
        header += comments + [""]
    text = "\n".join(header + lines + foot) + "\n"
    st._atomic_write(target, text)
    if verbose:
        n_rows = sum(1 for l in lines if l.startswith("| ") and "对象ID" not in l and "---" not in l)
        print(f"已写入骨架: {target}（{n_rows} 行待填 / {len(seen_ids)} 个对象）")
    return target


def main():
    """入口。写模式在 _run() 里，外面接住写锁失败（W6.2）。"""
    try:
        return _run()
    except StateLockError as e:
        print(f"\n错误：{e}", file=sys.stderr)
        sys.exit(3)


def _run():
    ap = argparse.ArgumentParser(description="只读状态快照（从基线折叠履历，不调 LLM）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--volume-dir", help="卷目录：折叠到该卷最后一章（含）")
    g.add_argument("--at-chapter", help="章目录：折叠到该章之前（不含）= 该章开篇状态")
    g.add_argument("--write-chapter-openers", metavar="小说目录",
                   help="为每章生成/刷新 00_开篇状态.md（按细纲「## 出场对象」裁剪）")
    g.add_argument("--changelog-skeleton", metavar="章目录",
                   help="生成预填的 01_状态履历.md 骨架（对象/字段/类型/变更类型已排好，只需填「值」）")
    ap.add_argument("--novel-dir", help="小说根目录（缺省自动定位）")
    ap.add_argument("--output", help="输出文件路径")
    ap.add_argument("--force", action="store_true",
                    help="--changelog-skeleton：覆盖已存在的非空 01_状态履历.md")
    args = ap.parse_args()

    if args.changelog_skeleton:
        build_changelog_skeleton(args.changelog_skeleton,
                                 novel_dir=os.path.abspath(args.novel_dir) if args.novel_dir else None,
                                 force=args.force)
        return

    if args.write_chapter_openers:
        nd = os.path.abspath(args.write_chapter_openers)
        nd = nd if os.path.isdir(os.path.join(nd, "05_工作区")) else st.find_novel_dir(nd)
        if not nd:
            print("错误: 无法定位小说根目录")
            sys.exit(1)
        # W6.2：--write-chapter-openers 会覆盖各章派生视图，同样要独占；
        # 其余模式（打快照到 stdout/文件）是只读的，不加锁。
        acquire_until_exit(os.path.join(nd, "05_工作区", "02_状态"),
                           tool="build_state_snapshot.py --write-chapter-openers")
        w = write_chapter_openers(nd)
        print(f"共刷新 {len(w)} 个 00_开篇状态.md")
        return

    anchor = os.path.abspath(args.volume_dir or args.at_chapter)
    novel_dir = os.path.abspath(args.novel_dir) if args.novel_dir else st.find_novel_dir(anchor)
    if not novel_dir:
        print("错误: 无法定位小说根目录，请用 --novel-dir 指定")
        sys.exit(1)

    changelogs = st.iter_workspace_changelogs(novel_dir)

    if args.volume_dir:
        vol = os.path.normpath(anchor)
        in_vol = [p for p in changelogs if os.path.normpath(p).startswith(vol + os.sep)]
        if not in_vol:
            print(f"警告: 卷目录下没有任何 {CHANGELOG_FILENAME}: {vol}")
            sys.exit(1)
        last_idx = changelogs.index(in_vol[-1])
        paths = changelogs[:last_idx + 1]
        vol_name = os.path.basename(vol)
        title = f"卷末状态快照 · {vol_name} (只读物化视图)"
        default_out = os.path.join(vol, "99_卷末状态快照.md")
    else:
        target_cl = os.path.normpath(os.path.join(anchor, CHANGELOG_FILENAME))
        if target_cl in changelogs:
            idx = changelogs.index(target_cl)
        else:
            # 该章可能还没建履历——折叠到「排序位置之前」的全部章
            idx = len([p for p in changelogs if p < target_cl])
        paths = changelogs[:idx]
        chap_name = st.chapter_rel_name(target_cl, novel_dir)
        title = f"开篇状态快照 · {chap_name} (只读)"
        default_out = None

    records = _fold(novel_dir, paths)
    rendered = render_md_table(records, title=title)

    out = args.output or default_out
    if out:
        st._atomic_write(out, rendered)
        print(f"已写入: {out}（{len(records)} 条记录，折叠 {len(paths)} 章）")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
