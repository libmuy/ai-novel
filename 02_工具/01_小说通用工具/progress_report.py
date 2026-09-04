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

    --write   把派生视图写到 05_工作区/02_状态/05_进度派生视图.md（默认只打印对账）
    --strict  有对账项时返回非 0

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


@dataclass
class Chapter:
    part: int
    volume: int
    number: int
    outline: Path | None = None
    manuscript: Path | None = None
    chapter_ws: Path | None = None
    words: int = 0
    cold_rounds: int = 0        # 校验记录里 `## 冷读…` 分节数（标题命名不统一，非逻辑轮次）
    revision_rounds: int = 0
    has_changelog: bool = False
    has_opener: bool = False
    merged: bool = False
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
            if pr.is_dir():
                c.revision_rounds = len(list(pr.glob("01_正文生成_修订*.md")))

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
    for c in rep.chapters:
        if c.declared_manuscript == "定稿":
            if not c.has_changelog:
                out.append(("warning", "PROGRESS003",
                            f"{c.cid} 正文标「定稿」，但本章缺 `02_状态/01_状态履历.md`"))
            elif not c.merged:
                out.append(("warning", "PROGRESS003",
                            f"{c.cid} 正文标「定稿」且有履历，但未折叠进 `01_最新状态/`"
                            f"（当前折叠至 {rep.merged_upto}）——跑 `merge_chapter_state.py`"))
        if c.declared_manuscript in ("定稿", "待校验") and c.cold_rounds == 0:
            out.append(("warning", "PROGRESS003",
                        f"{c.cid} 正文标「{c.declared_manuscript}」，但无冷读记录"
                        f"（`02_状态/02_正文校验记录.md` 无 `## 冷读` 分节）"))
    return out


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
        "| 章 | 细纲 | 声明 | 正文(汉字) | 声明 | 冷读记录节 | 云端返修 | 履历 | 开篇状态 | 已折叠 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in rep.chapters:
        L.append("| {cid} | {o} | {od} | {w} | {md} | {cr} | {rr} | {cl} | {op} | {mg} |".format(
            cid=c.cid,
            o="✔" if c.outline else "—", od=c.declared_outline or "—",
            w=c.words or "—", md=c.declared_manuscript or "—",
            cr=c.cold_rounds or "—", rr=c.revision_rounds or "—",
            cl="✔" if c.has_changelog else "—",
            op="✔" if c.has_opener else "—",
            mg="✔" if c.merged else "—"))
    if not rep.chapters:
        L.append("| （尚无章节） | — | — | — | — | — | — | — | — | — |")

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
        L.append(f"  {c.cid}  细纲={c.declared_outline or '未登记'}"
                 f"  正文={c.declared_manuscript or '未登记'}"
                 f" {c.words or 0} 字  冷读记录 {c.cold_rounds} 节"
                 f"  返修 {c.revision_rounds} 轮"
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
    args = ap.parse_args()

    novel_dir = Path(args.novel_dir).resolve()
    if not novel_dir.is_dir():
        print(f"目录不存在：{novel_dir}", file=sys.stderr)
        return 1

    rep = collect(novel_dir)

    if args.format == "json":
        print(json.dumps({
            "novel": rep.novel_name,
            "merged_upto": rep.merged_upto,
            "chapters": [{
                "章": c.cid, "细纲声明": c.declared_outline, "正文声明": c.declared_manuscript,
                "汉字": c.words, "冷读记录节": c.cold_rounds, "返修轮": c.revision_rounds,
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
