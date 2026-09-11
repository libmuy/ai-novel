#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进度派生视图与对账 (progress_report.py)

`00_进度.md` 长期是全仓 churn 最高的文件，且它自己写过「此前本文件长期滞后于
实际进度」。病因是它把两类信息混在一起：

- **可推导的**：文件在不在、多少字、冷读跑了几轮、云端返修几轮、履历折叠到哪一章
  ——这些脚本一秒算得出，人手抄只会越抄越旧。
- **不可推导的**：成熟度（草稿 / 待校验 / 定稿）与用户裁决——「通过全部校验」是
  人的判断，没有任何脚本能替它拍板。

本脚本把前一半物化成**派生视图**（`00_系统架构规范.md` §二·A 第 2 条允许的形态：
脚本生成、文件头注明派生、非权威），并对后一半做**对账**——进度表声明的成熟度
与可观测事实矛盾时逐条报出来。`00_进度.md` 从此只需维护成熟度与裁决说明。

用法
----
    progress_report.py <小说目录> [--write] [--format text|json]
    progress_report.py <小说目录> --preflight 细纲 <章号>

    --write       把派生视图写到 05_工作区/02_状态/05_进度派生视图.md（默认只打印对账）
    --strict      有对账项时返回非 0
    --preflight   细纲定稿前收敛检查：结构（PLAN023）+ 引用（REF003 等）+
                  内部标识泄漏（OUTLINE_LEAK001）+ 冷读记录，只看目标章、一次性
                  给单一 pass/fail 与逐条修复清单。取代弱模型手动分别跑
                  audit_consistency.py --rule planning/reference/outline_leak
                  再肉眼核对结构那一套。退出码 0=PASS，1=FAIL。

对账项也由 `audit_consistency.py` 的 `progress` 规则（PROGRESS001/002）执行，
所以 `check.sh` 会自动拦住漂移；本脚本额外给出人能读的全景表。
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DERIVED_REL = "05_工作区/02_状态/05_进度派生视图.md"
PROGRESS_REL = "00_进度.md"
SYNC_REL = "05_工作区/02_状态/01_最新状态/00_同步状态.md"

# 正文字数口径：汉字数（不含标题行）。与 00_进度.md 历来的记法一致。
_HAN_RE = re.compile(r"[一-鿿]")
_CODE_PATH_RE = re.compile(r"`([^`\n]+?\.md)`")
_STATUS_RE = re.compile(r"(定稿|待校验|草稿)")
# 进度表里的占位/通配路径，不参与「文件必须存在」的对账
_PLACEHOLDER = ("0N", "NN", "XX", "《", "*", "N.md")
# canonical 产出根：只有这些前缀的路径才是「正式小说数据」，对账只管它们
CANONICAL_PREFIXES = ("01_设定/", "02_数据库/", "03_规划/", "10_正文/")


def han_count(text: str) -> int:
    body = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
    return len(_HAN_RE.findall(body))


_ANCHOR_QUOTE_RE = re.compile(r"「([^」]+)」")
_ANCHOR_LINE_RE = re.compile(r"^[\s→]*锚点[:：]")  # 只认真正的「→ 锚点：」行，
# 不认某条 bullet 描述文字里顺带提到"锚点"两个字（比如引卷纲自己的数值批注）
_WS_RE = re.compile(r"\s+")
_ANCHOR_ELISION_RE = re.compile(r"……|…")  # 锚点句里的省略号＝"中间还有别的字，跳过"，不是逐字标点


def stale_landing_anchors(landing_check_text: str, manuscript_text: str) -> list[str]:
    """`03_细纲落地核对.md` 里已勾选（非 ❌未落地/豁免）的 `「锚点句」` 是否还能在
    当前正文里找到——找不到多半是核对表生成之后正文又被改了句子（落地核对锚点变成了
    幽灵引用），不是"漏勾选"那类会被 PROGRESS005 拦住的问题，需要单独报出来。

    锚点句里的 `……`/`…` 是人工写核对表时的**省略号惯例**（"中间还有别的字，跳过不引"），
    不代表正文里真有这三个点；因此按 `……` 切成若干段，只要求每段各自在正文里出现
    （不要求相邻/顺序），比死板的整串匹配更贴合实际写法，也更不容易误报。
    """
    norm_ms = _WS_RE.sub("", manuscript_text)
    stale: list[str] = []
    for ln in landing_check_text.splitlines():
        s = ln.strip()
        if not _ANCHOR_LINE_RE.match(s):
            continue  # 只处理真正的「→ 锚点：」行——排除文件头说明行和 bullet 正文里顺带提到"锚点"的情况
        if "❌未落地" in s or "豁免" in s:
            continue
        for q in _ANCHOR_QUOTE_RE.findall(s):
            parts = [p for p in (_WS_RE.sub("", seg) for seg in _ANCHOR_ELISION_RE.split(q)) if p]
            if parts and all(p in norm_ms for p in parts):
                continue
            stale.append(q)
    return stale


@dataclass
class Chapter:
    part: int
    volume: int
    number: int
    outline: Path | None = None
    manuscript: Path | None = None
    chapter_ws: Path | None = None
    words: int = 0
    cold_rounds: int = 0        # 正文校验记录里 `## 冷读…` 分节数（标题命名不统一，非逻辑轮次）
    outline_cold_rounds: int = 0  # 细纲对照记录里 `## 冷读…` 分节数
    revision_rounds: int = 0
    has_changelog: bool = False
    has_opener: bool = False
    merged: bool = False
    has_landing_check: bool = False    # 02_状态/03_细纲落地核对.md 存在
    landing_check_open: bool = False   # 该表仍有未锚定项（未勾选复选框 / 残留占位符 / 未 waive 的 ❌未落地）
    landing_check_stale: list[str] = field(default_factory=list)  # 锚点句在当前正文里找不到逐字匹配
    declared_outline: str | None = None
    declared_manuscript: str | None = None

    @property
    def cid(self) -> str:
        return f"章{self.number:04d}"


@dataclass
class Report:
    novel_dir: Path
    novel_name: str
    chapters: list[Chapter] = field(default_factory=list)
    settings: list[tuple[str, int, str | None]] = field(default_factory=list)
    db: list[tuple[str, int, int]] = field(default_factory=list)
    merged_upto: str = "—"
    state_objects: int = 0
    findings: list[tuple[str, str, str]] = field(default_factory=list)  # (级别, 代码, 说明)


# ────────────────────────────────────────────────────── 进度表解析

def declared_status(novel_dir: Path) -> dict[str, str]:
    """`00_进度.md` 表格行 → {路径: 成熟度}。与 prompt_build.progress 同口径。"""
    src = novel_dir / PROGRESS_REL
    out: dict[str, str] = {}
    if not src.exists():
        return out
    for line in src.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2 or all(set(c) <= set(":- ") for c in cells):
            continue
        paths = [p for c in cells for p in _CODE_PATH_RE.findall(c)]
        status = next((m.group(1) for c in cells
                       for m in [_STATUS_RE.search(c)] if m), None)
        if not paths or status is None:
            continue
        for p in paths:
            out.setdefault(p.strip(), status)
    return out


def lookup(declared: dict[str, str], path: Path, novel_dir: Path) -> str | None:
    """按后缀匹配查成熟度（进度表里既有全路径也有裸文件名）。"""
    try:
        want = path.relative_to(novel_dir).as_posix()
    except ValueError:
        want = path.as_posix()
    for recorded, st in declared.items():
        r = recorded.lstrip("./")
        if want == r or want.endswith("/" + r):
            return st
    return None


# ────────────────────────────────────────────────────── 采集

def collect(novel_dir: Path) -> Report:
    rep = Report(novel_dir=novel_dir, novel_name=novel_dir.name.split("_", 1)[-1])
    declared = declared_status(novel_dir)

    # ── 设定层
    for f in sorted((novel_dir / "01_设定").glob("*.md")):
        rep.settings.append((f.name, f.stat().st_size, lookup(declared, f, novel_dir)))

    # ── 资料层：每个分类的「总索引 + 子文件数」
    db = novel_dir / "02_数据库"
    if db.is_dir():
        for d in sorted(p for p in db.iterdir() if p.is_dir()):
            files = sorted(d.glob("*.md"))
            total = sum(f.stat().st_size for f in files)
            rep.db.append((d.name, len(files), total))

    # ── 章节流水线
    chapters: dict[tuple[int, int, int], Chapter] = {}

    def ch(part, vol, num) -> Chapter:
        key = (part, vol, num)
        if key not in chapters:
            chapters[key] = Chapter(part, vol, num)
        return chapters[key]

    for f in sorted((novel_dir / "03_规划").rglob("规划_卷*_章*.md")):
        m = re.search(r"卷0*(\d+)_章0*(\d+)", f.name)
        mp = re.search(r"第0*(\d+)部", str(f))
        if m:
            c = ch(int(mp.group(1)) if mp else 1, int(m.group(1)), int(m.group(2)))
            c.outline = f
            c.declared_outline = lookup(declared, f, novel_dir)

    for f in sorted((novel_dir / "10_正文").rglob("章*.md")):
        m = re.search(r"章0*(\d+)", f.name)
        mv = re.search(r"卷0*(\d+)", str(f))
        mp = re.search(r"第0*(\d+)部", str(f))
        if m:
            c = ch(int(mp.group(1)) if mp else 1, int(mv.group(1)) if mv else 1, int(m.group(1)))
            c.manuscript = f
            c.words = han_count(f.read_text(encoding="utf-8", errors="ignore"))
            c.declared_manuscript = lookup(declared, f, novel_dir)

    ws = novel_dir / "05_工作区"
    if ws.is_dir():
        for d in sorted(ws.rglob("*_章*")):
            if not d.is_dir():
                continue
            m = re.search(r"章0*(\d+)$", d.name)
            mv = re.search(r"卷0*(\d+)", str(d))
            mp = re.search(r"第0*(\d+)部", str(d))
            if not m:
                continue
            c = ch(int(mp.group(1)) if mp else 1, int(mv.group(1)) if mv else 1, int(m.group(1)))
            c.chapter_ws = d
            st, pr = d / "02_状态", d / "00_提示词"
            c.has_changelog = (st / "01_状态履历.md").exists()
            c.has_opener = (st / "00_开篇状态.md").exists()
            rec = st / "02_正文校验记录.md"
            if rec.exists():
                c.cold_rounds = len(re.findall(
                    r"^##\s*冷读", rec.read_text(encoding="utf-8", errors="ignore"), re.M))
            orec = st / "03_细纲对照记录.md"
            if orec.exists():
                c.outline_cold_rounds = len(re.findall(
                    r"^##\s*冷读", orec.read_text(encoding="utf-8", errors="ignore"), re.M))
            if pr.is_dir():
                c.revision_rounds = len(list(pr.glob("01_正文生成_修订*.md")))

            lc = st / "03_细纲落地核对.md"
            c.has_landing_check = lc.exists()
            if c.has_landing_check:
                lc_text = lc.read_text(encoding="utf-8", errors="ignore")
                # `>` 引用行是给人看的说明（本身含 `❌未落地` 等字样），不参与判定
                body = [ln for ln in lc_text.splitlines() if not ln.lstrip().startswith(">")]
                c.landing_check_open = (
                    any(re.match(r"\s*-\s*\[\s*\]\s+\S", ln) for ln in body)
                    or any("〔待填：正文" in ln for ln in body)
                    or any("❌未落地" in ln and "waive" not in ln.lower() and "豁免" not in ln
                           for ln in body))
                # `10_正文` 循环在本循环之前跑过，c.manuscript 此刻已就位（若存在）
                if c.manuscript is not None:
                    c.landing_check_stale = stale_landing_anchors(
                        lc_text, c.manuscript.read_text(encoding="utf-8", errors="ignore"))

    # ── 状态层折叠进度
    sync = novel_dir / SYNC_REL
    if sync.exists():
        txt = sync.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"折叠至章[:：]\s*(\S+)", txt)
        if m:
            rep.merged_upto = m.group(1)
        m2 = re.search(r"对象总数[:：]\s*(\d+)", txt)
        if m2:
            rep.state_objects = int(m2.group(1))
    # 折叠标记形如 `03_第01部/03_卷01/03_章0001`，部/卷/章俱全。
    # 按 (部, 卷, 章) 元组比大小，而不是只比章号——跨卷章号是否全局连续，
    # 数据里还没定死（卷 2~4 目前只有【基础定位】），别替它假设。
    mk_p = re.search(r"第0*(\d+)部", rep.merged_upto)
    mk_v = re.search(r"卷0*(\d+)", rep.merged_upto)
    mk_c = re.search(r"章0*(\d+)", rep.merged_upto)
    if mk_c is None:
        # 没有折叠记录（新书写 `__none__`），一章都还没并入
        for c in chapters.values():
            c.merged = False
    elif mk_p and mk_v:
        merged_key = (int(mk_p.group(1)), int(mk_v.group(1)), int(mk_c.group(1)))
        for c in chapters.values():
            c.merged = (c.part, c.volume, c.number) <= merged_key
    else:
        # 标记只写了章号、没写部/卷时退回按章号比。别因为标记写得简略就
        # 把所有章都判成「未折叠」——那会让 PROGRESS003 全线误报。
        merged_n = int(mk_c.group(1))
        for c in chapters.values():
            c.merged = c.number <= merged_n
    rep.chapters = sorted(chapters.values(), key=lambda c: (c.part, c.volume, c.number))

    rep.findings = reconcile(novel_dir, declared, rep)
    return rep


# ────────────────────────────────────────────────────── 对账

def reconcile(novel_dir: Path, declared: dict[str, str], rep: Report) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []

    # PROGRESS001：声明了成熟度的 canonical 产出，文件却不存在
    for path, status in sorted(declared.items()):
        p = path.lstrip("./")
        if not p.startswith(CANONICAL_PREFIXES):
            continue                      # 说明列里提到的裸文件名/工作区文件，不对账
        if any(x in p for x in _PLACEHOLDER):
            continue                      # 0N_卷0N 这类通配写法
        if not (novel_dir / p).exists():
            out.append(("error", "PROGRESS001",
                        f"`00_进度.md` 声明「{status}」的产出不存在：`{p}`"))

    # PROGRESS002：章节产物已落位，进度表却完全没登记
    for c in rep.chapters:
        if c.outline is not None and c.declared_outline is None:
            out.append(("warning", "PROGRESS002",
                        f"{c.cid} 细纲已落位但 `00_进度.md` 未登记："
                        f"`{c.outline.relative_to(novel_dir).as_posix()}`"))
        if c.manuscript is not None and c.declared_manuscript is None:
            out.append(("warning", "PROGRESS002",
                        f"{c.cid} 正文已落位但 `00_进度.md` 未登记："
                        f"`{c.manuscript.relative_to(novel_dir).as_posix()}`"))

    # PROGRESS003：声明「定稿」但流水线上还缺件（成熟度显然超前于事实）
    #   「定稿」＝「校验通过，可被引用」（见 `00_进度.md` 图例）——缺件即伪造下游前置，判 error。
    #   「待校验」＝结构齐但校验未做完，是正当中间态——只对「连正文都没落位」这类硬矛盾报 warning。
    for c in rep.chapters:
        # 细纲：定稿前必须跑过冷读循环并留记录（细纲缺陷会原样复制进之后每一版正文）
        if c.declared_outline == "定稿" and c.outline_cold_rounds == 0:
            out.append(("error", "PROGRESS003",
                        f"{c.cid} 细纲标「定稿」，但无冷读记录"
                        f"（`02_状态/03_细纲对照记录.md` 缺失或无 `## 冷读` 分节）——"
                        f"细纲门禁比正文严，`review_manuscript.py --mode outline` 冷读循环未跑就转定稿即伪造前置"))
        elif c.declared_outline == "待校验" and c.outline_cold_rounds == 0:
            out.append(("warning", "PROGRESS003",
                        f"{c.cid} 细纲标「待校验」，还没有冷读记录——细纲冷读循环尚未开始"))

        if c.declared_manuscript == "定稿":
            if not c.has_changelog:
                out.append(("error", "PROGRESS003",
                            f"{c.cid} 正文标「定稿」，但本章缺 `02_状态/01_状态履历.md`"))
            elif not c.merged:
                out.append(("error", "PROGRESS003",
                            f"{c.cid} 正文标「定稿」且有履历，但未折叠进 `01_最新状态/`"
                            f"（当前折叠至 {rep.merged_upto}）——跑 `merge_chapter_state.py`"))
            if c.cold_rounds == 0:
                out.append(("error", "PROGRESS003",
                            f"{c.cid} 正文标「定稿」，但无冷读记录"
                            f"（`02_状态/02_正文校验记录.md` 无 `## 冷读` 分节）——"
                            f"「定稿」＝校验通过，冷读循环未跑就转定稿即伪造前置"))
        elif c.declared_manuscript == "待校验" and c.cold_rounds == 0:
            out.append(("warning", "PROGRESS003",
                        f"{c.cid} 正文标「待校验」，还没有冷读记录——校验循环（`review_manuscript.py`）尚未开始"))

        # PROGRESS005：细纲落地核对表（步骤 3.5）缺失或仍有未锚定项
        if c.declared_manuscript == "定稿":
            if not c.has_landing_check:
                out.append(("error", "PROGRESS005",
                            f"{c.cid} 正文标「定稿」但缺细纲落地核对表 `02_状态/03_细纲落地核对.md`——"
                            f"跑 `build_landing_checklist.py <本章目录>` 生成、逐条锚定"))
            elif c.landing_check_open:
                out.append(("error", "PROGRESS005",
                            f"{c.cid} 细纲落地核对表仍有未锚定项"
                            f"（未勾选复选框 / 残留占位符 / 未 waive 的 `❌未落地`）——清完再转定稿"))
        elif c.declared_manuscript == "待校验" and c.has_landing_check and c.landing_check_open:
            out.append(("warning", "PROGRESS005",
                        f"{c.cid} 细纲落地核对表已生成但还有未锚定项——转定稿前要清完"))

        # PROGRESS006：落地核对表已勾选，但锚点句在当前正文里逐字找不到（幽灵锚点）。
        #   典型成因——生成核对表在前、之后又对正文做了一轮本地精修/冷读改稿，
        #   改动没有回头同步核对表，锚点句就指向了一句已经不存在的话。
        #   不属于"漏勾选"（PROGRESS005 管不到），必须单独报，否则核对表会悄悄失真。
        if c.has_landing_check and c.landing_check_stale:
            sev = "error" if c.declared_manuscript == "定稿" else "warning"
            sample = "；".join(f"「{q}」" for q in c.landing_check_stale[:3])
            more = f" 等共 {len(c.landing_check_stale)} 处" if len(c.landing_check_stale) > 3 else ""
            out.append((sev, "PROGRESS006",
                        f"{c.cid} 落地核对表里 {len(c.landing_check_stale)} 处锚点句在当前正文里找不到"
                        f"逐字匹配（{sample}{more}）——多半是核对表生成后正文又被改了，"
                        f"锚点没跟着回核，需要重新核对该表"))
    return out


# ────────────────────────────────────────────────────── 细纲定稿前一把过（--preflight 细纲 N）

def preflight_outline(novel_dir: Path, number: int) -> tuple[bool, str]:
    """结构 + 引用 + leak + 冷读记录一把跑，只看目标章，单一 pass/fail + 修复清单。

    取代之前弱模型要分别跑 `audit_consistency.py --rule planning/reference/
    outline_leak`（还得肉眼在一堆全书 Finding 里找出属于这一章的）+
    `progress_report.py` 看冷读记录三件事。三条 audit 规则本身要靠全书上下文
    解析引用/索引，所以规则照常跑全量，只是把结果按本章细纲文件路径过滤。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from audit import AuditEngine, AuditContext
    from audit.rules.reference import ReferenceRule
    from audit.rules.planning import PlanningRule
    from audit.rules.outline_leak import OutlineLeakRule

    rep = collect(novel_dir)
    candidates = [c for c in rep.chapters if c.number == number and c.outline is not None]
    if not candidates:
        return False, (f"未找到 章{number:04d} 的细纲文件"
                        f"（03_规划/**/规划_卷*_章{number:04d}.md）")
    if len(candidates) > 1:
        found = "、".join(f"`{c.outline.relative_to(novel_dir).as_posix()}`" for c in candidates)
        return False, (f"章{number:04d} 匹配到多个细纲文件（跨部/卷同号），"
                        f"preflight 只认单一路径，需手动指认：{found}")
    c = candidates[0]
    rel = c.outline.relative_to(novel_dir).as_posix()

    engine = AuditEngine(novel_dir)
    engine.register_rule(ReferenceRule())
    engine.register_rule(PlanningRule())
    engine.register_rule(OutlineLeakRule())
    all_findings = engine.run(context=AuditContext(novel_dir))
    findings = [f for f in all_findings if f.file == rel]

    checks = [
        ("结构（planning）", [f for f in findings if f.rule == "planning"]),
        ("引用（reference）", [f for f in findings if f.rule == "reference"]),
        ("内部标识泄漏（outline_leak）", [f for f in findings if f.rule == "outline_leak"]),
    ]

    ok = True
    L = [f"=== {c.cid} 细纲定稿前检查（{rel}）==="]
    for label, fs in checks:
        if not fs:
            L.append(f"  ✔ {label}：0 项")
            continue
        ok = False
        L.append(f"  ✘ {label}：{len(fs)} 项")
        for f in fs:
            loc = f"第{f.line}行 " if f.line else ""
            L.append(f"      [{f.severity}] {f.code} {loc}{f.message}")
            if f.suggestion:
                L.append(f"        → {f.suggestion}")

    if c.outline_cold_rounds > 0:
        L.append(f"  ✔ 冷读记录：{c.outline_cold_rounds} 节")
    else:
        ok = False
        L.append("  ✘ 冷读记录：0 节（`02_状态/03_细纲对照记录.md` 缺失或无 `## 冷读` 分节）"
                  "——先跑 `review_manuscript.py --mode outline` 冷读循环")

    L.append("")
    L.append("PASS" if ok else "FAIL")
    return ok, "\n".join(L)


# ────────────────────────────────────────────────────── 渲染

def render_derived(rep: Report) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    L = [
        f"# {rep.novel_name} · 进度派生视图",
        "",
        "> **派生 · 禁止手工编辑 · 非权威**",
        f"> 由 `02_工具/01_小说通用工具/progress_report.py --write` 生成于 {now}。",
        ">",
        "> 本文件只记录**可观测事实**（文件在不在、多少字、跑了几轮、折叠到哪）。",
        "> **成熟度（草稿 / 待校验 / 定稿）是人的判断，权威在 `00_进度.md`**——",
        "> 「通过全部校验」没有任何脚本能替你拍板。两边不一致时见下方【对账】，",
        "> 并以 `00_进度.md` 为准去修事实，或修 `00_进度.md` 的声明。",
        "",
        "---",
        "",
        "## 一、章节流水线",
        "",
        "| 章 | 细纲 | 声明 | 细纲冷读节 | 正文(汉字) | 声明 | 冷读记录节 | 云端返修 | 落地核对 | 履历 | 开篇状态 | 已折叠 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in rep.chapters:
        if not c.has_landing_check:
            lc = "—"
        elif c.landing_check_open:
            lc = "○"          # 已生成、仍有未锚定项
        else:
            lc = "✔"
        L.append("| {cid} | {o} | {od} | {ocr} | {w} | {md} | {cr} | {rr} | {lc} | {cl} | {op} | {mg} |".format(
            cid=c.cid,
            o="✔" if c.outline else "—", od=c.declared_outline or "—",
            ocr=c.outline_cold_rounds or "—",
            w=c.words or "—", md=c.declared_manuscript or "—",
            cr=c.cold_rounds or "—", rr=c.revision_rounds or "—",
            lc=lc,
            cl="✔" if c.has_changelog else "—",
            op="✔" if c.has_opener else "—",
            mg="✔" if c.merged else "—"))
    if not rep.chapters:
        L.append("| （尚无章节） | — | — | — | — | — | — | — | — | — | — | — |")

    L += ["", f"状态树折叠至 **{rep.merged_upto}**，共 {rep.state_objects} 个对象。", "",
          "## 二、设定层", "", "| 文件 | 体量 | 进度表声明 |", "|---|---|---|"]
    for name, size, st in rep.settings:
        L.append(f"| `{name}` | {size / 1024:.1f} KB | {st or '—'} |")

    L += ["", "## 三、资料层", "", "| 分类 | 文件数 | 体量 |", "|---|---|---|"]
    for name, n, size in rep.db:
        L.append(f"| `{name}` | {n} | {size / 1024:.1f} KB |")

    L += ["", "## 四、对账", ""]
    if not rep.findings:
        L.append("进度表声明与可观测事实一致，无对账项。")
    else:
        L.append("| 级别 | 代码 | 说明 |")
        L.append("|---|---|---|")
        for lv, code, msg in rep.findings:
            L.append(f"| {lv} | `{code}` | {msg} |")
        L += ["", "> 规则代码定义见技能 `03_任务技能/02_小说级/01_项目状态审计.md`。"]
    return "\n".join(L) + "\n"


def render_text(rep: Report) -> str:
    L = [f"=== {rep.novel_name} · 进度对账 ===",
         f"章节 {len(rep.chapters)} 个，状态树折叠至 {rep.merged_upto}", ""]
    for c in rep.chapters:
        L.append(f"  {c.cid}  细纲={c.declared_outline or '未登记'}（冷读 {c.outline_cold_rounds} 节）"
                 f"  正文={c.declared_manuscript or '未登记'}"
                 f" {c.words or 0} 字  冷读记录 {c.cold_rounds} 节"
                 f"  返修 {c.revision_rounds} 轮"
                 f"  落地核对{'✔' if c.has_landing_check and not c.landing_check_open else ('○' if c.has_landing_check else '✘')}"
                 f"  履历{'✔' if c.has_changelog else '✘'}"
                 f"  折叠{'✔' if c.merged else '✘'}")
    L.append("")
    if not rep.findings:
        L.append("对账：进度表声明与可观测事实一致。")
    else:
        L.append(f"对账：{len(rep.findings)} 项")
        for lv, code, msg in rep.findings:
            L.append(f"  [{lv.upper()}] {code} {msg}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成进度派生视图并与 00_进度.md 对账")
    ap.add_argument("novel_dir")
    ap.add_argument("--write", action="store_true",
                    help=f"写出派生视图到 {DERIVED_REL}")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--strict", action="store_true", help="有对账项时返回非 0")
    ap.add_argument("--preflight", nargs=2, metavar=("KIND", "N"),
                    help="细纲定稿前一把过：--preflight 细纲 <章号>（结构+引用+leak+冷读记录，"
                         "单一 pass/fail，与 --write/--format/--strict 互斥）")
    args = ap.parse_args()

    novel_dir = Path(args.novel_dir).resolve()
    if not novel_dir.is_dir():
        print(f"目录不存在：{novel_dir}", file=sys.stderr)
        return 1

    if args.preflight:
        kind, num_s = args.preflight
        if kind != "细纲":
            print(f"暂只支持 --preflight 细纲 <N>，收到 kind={kind!r}", file=sys.stderr)
            return 2
        try:
            number = int(num_s)
        except ValueError:
            print(f"章号需为整数，收到 {num_s!r}", file=sys.stderr)
            return 2
        ok, text = preflight_outline(novel_dir, number)
        print(text)
        return 0 if ok else 1

    rep = collect(novel_dir)

    if args.format == "json":
        print(json.dumps({
            "novel": rep.novel_name,
            "merged_upto": rep.merged_upto,
            "chapters": [{
                "章": c.cid, "细纲声明": c.declared_outline, "正文声明": c.declared_manuscript,
                "细纲冷读记录节": c.outline_cold_rounds,
                "汉字": c.words, "冷读记录节": c.cold_rounds, "返修轮": c.revision_rounds,
                "落地核对": c.has_landing_check and not c.landing_check_open,
                "落地核对未清": c.has_landing_check and c.landing_check_open,
                "履历": c.has_changelog, "已折叠": c.merged,
            } for c in rep.chapters],
            "findings": [{"severity": lv, "code": c, "message": m} for lv, c, m in rep.findings],
        }, ensure_ascii=False, indent=2))
    else:
        print(render_text(rep))

    if args.write:
        out = novel_dir / DERIVED_REL
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_derived(rep), encoding="utf-8")
        print(f"\n已写出派生视图：{DERIVED_REL}")

    if args.strict and rep.findings:
        return 1
    return 1 if any(lv == "error" for lv, _, _ in rep.findings) else 0


if __name__ == "__main__":
    sys.exit(main())
