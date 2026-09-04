# -*- coding: utf-8 -*-
"""
六段骨架拼装 (assemble.py)

骨架与各段职责的唯一规格来源是
`00_通用模板/04_提示词/00_云端提示词生成器.md`（【固定骨架】＋「与正文阶段任务的关系」），
本模块只做它的可执行实现，不在这里另立规则。

两条贯穿全模块的纪律：
1. **规则件全文内联，以文件名为区块标题**——不做「本章适用部分节录」。
   逐章手工节录正是历史上每章 141 KB 手工劳动的来源，且压缩口径逐章漂移。
2. **本工具不撰写小说内容**。凡拼装出的段落，要么是某个源文件的原文，
   要么是从结构化字段（节拍表行、出场对象表、场景表）机械导出的。
   需要作者判断的地方一律留 `>>> 待人工确认` 标记，绝不代笔。
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import extract, leak
from .layout import ChapterLayout, rel

TEMPLATES = "00_通用模板"


@dataclass
class Block:
    title: str
    body: str
    origin: str        # 逐字内联的源文件相对路径，或 "authored" / "extract:<path>"

    @property
    def authored(self) -> bool:
        return self.origin == "authored"


@dataclass
class Section:
    title: str
    blocks: list[Block] = field(default_factory=list)

    lettered: bool = False
    _next: int = 0

    def add(self, title: str, body: str, origin: str):
        if not (body and body.strip()):
            return
        if self.lettered:
            title = f"{chr(ord('A') + self._next)}. {title}"
            self._next += 1
        self.blocks.append(Block(title, body.rstrip() + "\n", origin))


@dataclass
class Prompt:
    header: str
    sections: list[Section]
    todos: list[str] = field(default_factory=list)
    # 产出是否为正文。泄漏自检针对的是「被转写成正文」的风险；细纲产出的是规划文档，
    # 里面本就该有编号与场次号，对它扫「内部标识」只会全是假阳性。
    prose_output: bool = True

    def render(self) -> str:
        parts = [self.header.rstrip(), ""]
        for sec in self.sections:
            parts.append(f"# {sec.title}")
            parts.append("")
            for b in sec.blocks:
                parts.append(f"## {b.title}")
                parts.append("")
                parts.append(b.body.rstrip())
                parts.append("")
        return "\n".join(parts).rstrip() + "\n"

    def leaks(self) -> list[leak.Leak]:
        if not self.prose_output:
            return []
        out = []
        for sec in self.sections:
            for b in sec.blocks:
                if b.authored:
                    out.append((sec.title, b))
        return [lk for title, b in out for lk in leak.scan(f"{title} · {b.title}", b.body)]

    def stats(self) -> dict:
        n_blocks = sum(len(s.blocks) for s in self.sections)
        n_verbatim = sum(1 for s in self.sections for b in s.blocks if not b.authored)
        return {"字节数": len(self.render().encode("utf-8")),
                "区块数": n_blocks, "逐字内联区块": n_verbatim,
                "待人工确认": len(self.todos)}


# ─────────────────────────────────────────────────────────── 取材上下文

@dataclass
class Ctx:
    novel_dir: Path
    repo_root: Path
    layout: ChapterLayout
    novel_name: str
    missing: list[str] = field(default_factory=list)

    def tpl(self, relpath: str) -> str:
        return self._read(self.repo_root / TEMPLATES / relpath, f"{TEMPLATES}/{relpath}")

    def data(self, relpath: str) -> str:
        return self._read(self.novel_dir / relpath, relpath)

    def read_path(self, p: Path) -> str:
        return self._read(p, rel(self.novel_dir, p))

    def _read(self, p: Path, label: str) -> str:
        if not p.exists():
            self.missing.append(label)
            return ""
        return p.read_text(encoding="utf-8", errors="ignore")


def _retarget(text: str, mapping: dict[str, str]) -> str:
    """`01_系统指令` 里的模板文件名指代 → 本提示词内的内联区块标题。

    依据「与正文阶段任务的关系」§5：不得在提示词里保留外部文件路径，
    否则模型会去「找文件」，或把路径当字面抄进正文。
    """
    # 长名优先，避免 `00_通用写作规则` 抢先吃掉 `00_通用写作规则_校验版`
    for name in sorted(mapping, key=len, reverse=True):
        block_title = mapping[name]
        text = re.sub(rf"`?{re.escape(name)}(?:\.md)?`?", f"本提示词「{block_title}」区块", text)
    return text


def _todo(ctx_todos: list[str], label: str, hint: str) -> str:
    ctx_todos.append(label)
    return f">>> 待人工确认：{label}\n>>> {hint}\n"


# ─────────────────────────────────────────────────────────── 公共段

ROLE_MANUSCRIPT = """你是《{novel}》的执笔者。严格按下文【已有数据】中的单章细纲逐场景写作，
不改变既定事实、不新增细纲以外的道具与机制、不跳过或合并场景。

模板与规则中出现的任何名称与案例仅用于展示格式，**不是本书的预设设定**；
在【已有数据】未明确指定时，禁止照抄示例名称，一律按《{novel}》的题材与基调自主原创。

你看不到本书的其它资料——本提示词内联的就是全部。缺什么就按细纲留白处理，**不要脑补**。
"""

ROLE_OUTLINE = """你是《{novel}》的规划师。按下文【必读模板】的字段结构产出本章细纲，
所有事实以【已有数据】为准，冲突时以【已有数据】为准；信息不足处标注「待确认」，**不要编造**。

模板与规则中出现的任何名称与案例仅用于展示格式，**不是本书的预设设定**；
在【已有数据】未明确指定时，禁止照抄示例名称，一律按《{novel}》的题材与基调自主原创。
"""

NO_INVENT = """> 细纲未列出的道具、机制、因果链、场景，一律不得自行添加。细纲留白处宁可不写，不得脑补。
> 新增任何名词性设定须在同章产生叙事效果且可登记（新角色 / 新伏笔 / 资源变更）。

""" + leak.NEGATIVE_CONSTRAINT_GUARD + "\n"


def _header(ctx: Ctx, archive: Path, backfill: Path, target: Path,
            task_label: str, followup: str) -> str:
    return f"""> **任务**：{task_label}
> **产出保存文件名**：`{backfill.name}`（存到 `{rel(ctx.novel_dir, backfill)}`，与提示词存档同名）
> **落位目标**：`{rel(ctx.novel_dir, target)}`
> **提示词存档**：`{rel(ctx.novel_dir, archive)}`
> **回填后本地流程**：{followup}
>
> 本提示词由 `02_工具/01_小说通用工具/build_prompt.py` 依当前定稿数据拼装，**自包含**：
> 云端不访问本仓库，所需模板与数据已全文内联。数据变化后须重新拼装，不得直接复用旧稿。
"""


# ─────────────────────────────────────────────────────────── 正文

def build_manuscript(ctx: Ctx) -> Prompt:
    L = ctx.layout
    todos: list[str] = []
    outline_text = ctx.read_path(L.outline)

    archive = L.prompt_dir / "01_正文生成.md"
    backfill = L.output_dir / "01_正文生成.md"

    header = _header(
        ctx, archive, backfill, L.manuscript, f"写《{ctx.novel_name}》本章正文",
        "落位正文 → 按【输出格式】的【待登记清单】回填本章 `02_状态/01_状态履历.md` → "
        "`merge_chapter_state.py --chapter-dir <本章目录>` → `audit_consistency.py` 复查 → "
        "`review_manuscript.py --chapter-dir <本章目录>` 起冷读循环。")

    # ── 【你的角色】
    s_role = Section("【你的角色】")
    s_role.add("角色与纪律", ROLE_MANUSCRIPT.format(novel=ctx.novel_name), "authored")

    # ── 【必读规则】：红线包打头，规则件全文内联
    s_rules = Section("【必读规则】")
    redline = ctx.data("01_设定/00_红线包.md")
    if redline:
        s_rules.add("常驻红线包（本书逐章不变的约束，整段照办）", redline, "01_设定/00_红线包.md")
    else:
        # 红线包未建 → 退回逐份摘抄的老做法（规格明确要求的降级路径）
        todos.append("本书尚无 `01_设定/00_红线包.md`，已退回「逐份摘抄」老做法，建议补建")
        for label, p in (("通用写作规则（生成版）", "01_写作规则/00_通用写作规则_生成版.md"),):
            s_rules.add(label, ctx.tpl(p), f"{TEMPLATES}/{p}")
        s_rules.add("本书文风差异", ctx.data("01_设定/00_文风.md"), "01_设定/00_文风.md")

    # 禁用词表全文内联。红线包 §六 只收「高频项摘录」，正文里写着「冲突以禁用词表为准」——
    # 但云端在零上下文里读不到那份表，这条指引对它是空的。表本身只有几 KB，
    # 相对整份提示词可以忽略；不内联的代价是「事后由 LEXICON001 检出再返修」，
    # 内联的收益是当场不写错。ch0002 通读新加的「油毡」就属于摘录没覆盖、
    # 但下一章仍会踩的那一类。
    lexicon = ctx.data("01_设定/00_禁用词表.md")
    if lexicon:
        s_rules.add("正文禁用词（完整清单，权威）", lexicon, "01_设定/00_禁用词表.md")

    sysinst = ctx.tpl("01_写作规则/01_系统指令.md")
    common = extract.read_section(sysinst, "通用指令（所有任务共用）")
    for h in ("核心原则", "文风红线", "世界基本法则红线", "版权与人设红线"):
        common += "\n" + extract.read_section(sysinst, h)
    s_rules.add("系统指令 · 通用", common, f"{TEMPLATES}/01_写作规则/01_系统指令.md")

    s_rules.add("单章细纲字段说明", ctx.tpl("02_卡片模板/07_单章细纲模板.md"),
                f"{TEMPLATES}/02_卡片模板/07_单章细纲模板.md")

    for label, tplpath in _event_templates(outline_text):
        s_rules.add(label, ctx.tpl(tplpath), f"{TEMPLATES}/{tplpath}")

    if 1 <= L.chapter <= 3:
        s_rules.add("开篇三章设计指南", ctx.tpl("01_写作规则/05_开篇三章设计指南.md"),
                    f"{TEMPLATES}/01_写作规则/05_开篇三章设计指南.md")

    # 逐字取自「示例去污染规则」第 6 条与「与正文阶段任务的关系」§6，非本工具撰写
    s_rules.add("禁脑补硬约束", NO_INVENT,
                f"{TEMPLATES}/04_提示词/00_云端提示词生成器.md")

    # ── 【已有数据】
    s_data = Section("【已有数据】", lettered=True)
    s_data.add("本章开篇状态（派生视图，禁止在正文中改写这些初值）",
               ctx.read_path(L.opener_state), rel(ctx.novel_dir, L.opener_state))
    s_data.add("本章细纲（逐场景执行，不跳过不合并）", outline_text, rel(ctx.novel_dir, L.outline))

    prot = extract.card_path(ctx.novel_dir, extract.Ref("主角", ""))
    if prot:
        s_data.add("主角档案", ctx.read_path(prot), rel(ctx.novel_dir, prot))

    _add_cast_cards(ctx, s_data, outline_text)

    concept = ctx.data("01_设定/00_小说概念.md")
    hard = extract.wr_rules(concept, ("硬",))
    if hard:
        body = ("以下为世界**硬规则**，本章不得违反：\n\n"
                "| 规则ID | 名称 | 状态 | 内容 |\n|---|---|---|---|\n" + "\n".join(hard) + "\n\n"
                + extract.read_section(concept, "信息与认知法则"))
        s_data.add("世界基本法则 · 硬规则清单", body, "extract:01_设定/00_小说概念.md")

    _add_dy_and_fh(ctx, s_data, outline_text)

    # ── 【任务】：执行要求逐字取自系统指令任务2，文件名指代改写为区块标题
    s_task = Section("【任务】")
    # 只取围栏内的「给云端的指令」；围栏后的「拼装为云端提示词时……」是给本地
    # Agent 的操作说明（含仓库路径与脚本名），不发给云端。
    task2 = extract.fenced_block(extract.read_section(sysinst, "任务2：写单章正文"))
    task2 = _retarget(task2, {
        # 「按 07_单章细纲模板 的场景列表写作」指的是**本章那份细纲**，不是模板的字段说明
        "07_单章细纲模板": "【已有数据】的本章细纲",
        # 「逐条检查 00_通用写作规则 第八章自检清单」→ 清单本体内联在【输出后自检】
        "00_通用写作规则": "【输出后自检】的单章一站式自检清单",
        "01_设定/00_小说概念.md": "【已有数据】的世界基本法则 · 硬规则清单",
        "00_通用模板/03_字段词表.md": "【输出格式】的【待登记清单】",
        "01_状态履历.md": "本章状态履历（本地 Agent 负责，云端不必关心）",
    })
    s_task.add("执行要求", task2, f"{TEMPLATES}/01_写作规则/01_系统指令.md")
    s_task.add("本章场次与字数预算", _scene_budget_table(outline_text, todos),
               f"extract:{rel(ctx.novel_dir, L.outline)}")

    # ── 【输出格式】/【输出后自检】（元指令段，允许出现文件名与编号）
    s_out = Section("【输出格式】")
    s_out.add("正文与附录", _output_format(), "authored")

    s_check = Section("【输出后自检】")
    s_check.add("单章一站式自检清单", ctx.tpl("01_写作规则/00_通用写作规则_校验版.md"),
                f"{TEMPLATES}/01_写作规则/00_通用写作规则_校验版.md")
    if 1 <= L.chapter <= 3:
        guide = ctx.tpl("01_写作规则/05_开篇三章设计指南.md")
        contract = extract.read_section(guide, "六、开篇三章契约自检清单") or \
            extract.read_section(guide, "六、契约自检清单")
        s_check.add("开篇三章契约自检", contract,
                    f"{TEMPLATES}/01_写作规则/05_开篇三章设计指南.md")
    s_check.add("脑补自检", "逐句检查——本段是否引入了细纲没有的东西？若有，删除或退回细纲层。\n",
                "authored")

    return Prompt(header, [s_role, s_rules, s_data, s_task, s_out, s_check], todos,
                  prose_output=True)


# ─────────────────────────────────────────────────────────── 单章细纲

def build_outline(ctx: Ctx) -> Prompt:
    L = ctx.layout
    todos: list[str] = []
    archive = L.prompt_dir / "00_单章细纲.md"
    backfill = L.output_dir / "00_单章细纲.md"

    header = _header(
        ctx, archive, backfill, L.outline, f"写《{ctx.novel_name}》本章细纲",
        "落位细纲到规划层 → `audit_consistency.py` 复查 → "
        "`review_manuscript.py --chapter-dir <本章目录> --mode outline` 起冷读循环"
        "（细纲门禁比正文严：必改项必须清零才能去拼正文提示词）。")

    s_role = Section("【你的角色】")
    s_role.add("角色与纪律", ROLE_OUTLINE.format(novel=ctx.novel_name), "authored")

    # ── 【必读模板】
    s_tpl = Section("【必读模板】")
    s_tpl.add("单章细纲模板", ctx.tpl("02_卡片模板/07_单章细纲模板.md"),
              f"{TEMPLATES}/02_卡片模板/07_单章细纲模板.md")
    s_tpl.add("通用写作规则（生成版）", ctx.tpl("01_写作规则/00_通用写作规则_生成版.md"),
              f"{TEMPLATES}/01_写作规则/00_通用写作规则_生成版.md")
    if 1 <= L.chapter <= 3:
        s_tpl.add("开篇三章设计指南", ctx.tpl("01_写作规则/05_开篇三章设计指南.md"),
                  f"{TEMPLATES}/01_写作规则/05_开篇三章设计指南.md")

    plan_text = ctx.read_path(L.volume_plan)
    beat = _beat_row(plan_text, L.chapter)
    for label, tplpath in _event_templates_from_beat(beat):
        s_tpl.add(label, ctx.tpl(tplpath), f"{TEMPLATES}/{tplpath}")

    # ── 【已有数据】
    s_data = Section("【已有数据】", lettered=True)
    s_data.add("本卷大纲 · 本章节拍", _beat_block(beat, todos),
               f"extract:{rel(ctx.novel_dir, L.volume_plan)}")

    prot = extract.card_path(ctx.novel_dir, extract.Ref("主角", ""))
    if prot:
        s_data.add("主角档案", ctx.read_path(prot), rel(ctx.novel_dir, prot))

    # 出场对象 = 节拍表本章摘要里的全部 @引用（`00_系统架构规范.md` §二·A「卷大纲的落地」）
    _add_cast_cards(ctx, s_data, beat.get("摘要", "") if beat else "", from_beat=True)

    concept = ctx.data("01_设定/00_小说概念.md")
    hard = extract.wr_rules(concept, ("硬",))
    if hard:
        s_data.add("世界基本法则 · 硬规则清单",
                   "| 规则ID | 名称 | 状态 | 内容 |\n|---|---|---|---|\n" + "\n".join(hard) + "\n",
                   "extract:01_设定/00_小说概念.md")

    _add_dy_and_fh(ctx, s_data, beat.get("摘要", "") if beat else "")

    s_data.add("上下文滑动窗口", _sliding_window(ctx, todos),
               "extract:上一章正文")
    s_data.add("本章开篇状态", ctx.read_path(L.opener_state) or
               _todo(todos, "本章开篇状态未物化",
                     "跑 `build_state_snapshot.py --write-chapter-openers`（首章用冻结基线）"),
               rel(ctx.novel_dir, L.opener_state))

    # ── 【任务目标】
    s_task = Section("【任务目标】")
    s_task.add("产出要求", _outline_task(beat), "authored")

    s_out = Section("【输出格式】")
    s_out.add("按模板字段全量输出", _outline_output_format(), "authored")

    s_check = Section("【验收自检】")
    s_check.add("自检清单", _outline_selfcheck(), "authored")

    return Prompt(header, [s_role, s_tpl, s_data, s_task, s_out, s_check], todos,
                  prose_output=False)


# ─────────────────────────────────────────────────────────── 辅助

_EVENT_TPL = {
    "战斗": ("战斗结算模板", "02_卡片模板/08_战斗结算模板.md"),
    "冲突": ("战斗结算模板", "02_卡片模板/08_战斗结算模板.md"),
    "突破": ("主角突破卡模板", "02_卡片模板/09_主角突破卡模板.md"),
    "晋升": ("主角突破卡模板", "02_卡片模板/09_主角突破卡模板.md"),
    "抉择": ("事件与感悟卡模板", "02_卡片模板/10_事件与感悟卡模板.md"),
    "道义": ("事件与感悟卡模板", "02_卡片模板/10_事件与感悟卡模板.md"),
    "闭环": ("事件与感悟卡模板", "02_卡片模板/10_事件与感悟卡模板.md"),
}


def _event_templates(outline_text: str) -> list[tuple[str, str]]:
    """细纲里有【战斗结算要素】/【突破】等区块 → 追加对应事件模板。"""
    out, seen = [], set()
    titles = " ".join(extract.section_titles(outline_text, max_level=3))
    for key, (label, path) in _EVENT_TPL.items():
        if key in titles and path not in seen:
            seen.add(path)
            out.append((label, path))
    return out


def _event_templates_from_beat(beat: Optional[dict]) -> list[tuple[str, str]]:
    """节拍表「必用模板」「核心事件类型」列 → 事件模板（合计 ≤1，见路由表「按需追加」）。"""
    if not beat:
        return []
    hay = f"{beat.get('必用模板', '')} {beat.get('核心事件类型', '')}"
    for key, (label, path) in _EVENT_TPL.items():
        if key in hay:
            return [(label, path)]
    return []


def _add_cast_cards(ctx: Ctx, sec: Section, source_text: str, from_beat: bool = False):
    """把出场对象逐个解析成卡片并全文内联。

    正文阶段取自细纲「## 出场对象」表；细纲阶段取自节拍表本章摘要的 `@引用`
    （`00_系统架构规范.md` §二·A：供任务11 检索的「本章出场对象」＝摘要里的全部 @引用）。
    """
    if not source_text:
        return
    if from_beat:
        entries = [extract.CastEntry(r, "", "") for r in extract.parse_refs(source_text)]
    else:
        entries = extract.parse_cast(source_text)
    if not entries:
        return

    if from_beat:
        roster_title = "本章出场对象（取自卷大纲节拍表摘要的 @引用，细纲须据剧情补全）"
        lead = ("以下为卷大纲节拍表本章摘要点名的对象。**这是下限不是全集**——"
                "本章实际登场 / 被提及 / 状态被改动的对象，细纲的「## 出场对象」表须补齐。\n\n")
    else:
        roster_title = "本章出场对象一览"
        lead = ""
    roster = ["| 对象 | 出场方式 | 卡片 |", "|---|---|---|"]
    cards: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for e in entries:
        if e.ref.ref_type in extract.STATE_ONLY_TYPES:
            roster.append(f"| {e.ref.render()} | {e.mode or '—'} | 状态对象，见「本章开篇状态」 |")
            continue
        p = extract.card_path(ctx.novel_dir, e.ref)
        if p is None:
            roster.append(f"| {e.ref.render()} | {e.mode or '—'} | — |")
            continue
        roster.append(f"| {e.ref.render()} | {e.mode or '—'} | 见下方内联卡片 |")
        if p not in seen and e.ref.ref_type != "主角":
            seen.add(p)
            cards.append((e.ref.name, p))

    sec.add(roster_title, lead + "\n".join(roster) + "\n", "extract:出场对象")
    for name, p in cards:
        sec.add(f"出场对象卡 · {name}", ctx.read_path(p), rel(ctx.novel_dir, p))


def _add_dy_and_fh(ctx: Ctx, sec: Section, source_text: str):
    refs = extract.parse_refs(source_text)
    dy_ids = [r.name for r in refs if r.ref_type == "道义"]
    fh_ids = [r.name for r in refs if r.ref_type == "伏笔"]

    # 细纲【道义与感悟】表里的「本章落地道义」也算
    m = re.search(r"DY-\d+", extract.read_section(source_text, "【道义与感悟】") or "")
    if m and m.group(0) not in dy_ids:
        dy_ids.append(m.group(0))

    if dy_ids:
        core = ctx.data("01_设定/05_核心道义.md")
        body = "\n\n".join(filter(None, (extract.dy_block(core, d) for d in dy_ids)))
        sec.add(f"本章道义 · {'、'.join(dy_ids)}", body, "extract:01_设定/05_核心道义.md")

    if fh_ids:
        ledger = ctx.data("03_规划/00_伏笔总纲.md")
        vol = ctx.read_path(ctx.layout.volume_foreshadow)
        rows = extract.ledger_rows(ledger, fh_ids) + extract.ledger_rows(vol, fh_ids)
        if rows:
            sec.add(f"本章伏笔 · {'、'.join(fh_ids)}",
                    "登记以本表为准，**禁止现编伏笔号**：\n\n" + "\n".join(rows) + "\n",
                    "extract:03_规划/00_伏笔总纲.md")


def _beat_row(plan_text: str, chapter: int) -> Optional[dict]:
    """卷大纲【章节节拍表】里本章那一行。"""
    sec = extract.read_section(plan_text, "【章节节拍表】")
    if not sec:
        return None
    header: list[str] = []
    for cells in extract.table_rows(sec):
        if cells and cells[0].strip() in ("章节", "章节号"):
            header = cells
            continue
        if not cells or not re.match(r"^第?0*\d+章?$", cells[0].strip()):
            continue
        m = re.search(r"(\d+)", cells[0])
        if not m or int(m.group(1)) != chapter:
            continue
        row = {"章节": cells[0]}
        names = header[1:] if header else ["摘要", "必用模板", "核心事件类型", "钩子类型"]
        for i, val in enumerate(cells[1:]):
            key = names[i] if i < len(names) else f"列{i}"
            row[key.replace("一句话剧情摘要", "摘要")] = val
        return row
    return None


def _beat_block(beat: Optional[dict], todos: list[str]) -> str:
    if not beat:
        return _todo(todos, "卷大纲【章节节拍表】里找不到本章行",
                     "先补齐卷大纲的本章节拍（摘要须用 @引用 点名关键对象/伏笔/资源）")
    lines = ["本章在卷大纲里的节拍（**摘要是本章事件归属的唯一权威**，逐项落地）：", ""]
    lines += [f"- **{k}**：{v}" for k, v in beat.items() if v]
    return "\n".join(lines) + "\n"


def _scene_budget_table(outline_text: str, todos: list[str]) -> str:
    scenes = extract.scene_blocks(outline_text)
    if not scenes:
        return _todo(todos, "细纲里没有【场景列表】", "先补细纲的场景切分与字数预算")
    rows = ["各场次与预算取自本章细纲，**逐场写满、误差不超过 ±20%**：", "",
            "| 场次 | 预算字数 | 功能 |", "|---|---|---|"]
    for title, body in scenes:
        words = ""
        func = ""
        m = re.search(r"(\d{2,4})\s*字", title + " " + body[:400])
        if m:
            words = m.group(1)
        m2 = re.search(r"功能[：:]\s*([^\n，,）)]+)", title + " " + body[:400])
        if m2:
            func = m2.group(1).strip()
        rows.append(f"| {title} | {words or '—'} | {func or '—'} |")
    total = sum(int(re.search(r"(\d{2,4})\s*字", t + ' ' + b[:400]).group(1))
                for t, b in scenes if re.search(r"(\d{2,4})\s*字", t + ' ' + b[:400]))
    if total:
        rows.append(f"\n全章合计预算 **{total} 字**。")
    return "\n".join(rows) + "\n"


def _output_format() -> str:
    return f"""输出 = 正文 ＋ 附录【待登记清单】。

**正文**：按细纲的场次顺序连续写出，场次之间空一行分隔，不加编号标题、不加旁白说明。

**附录【待登记清单】**（正文之后，用二级标题 `## 待登记清单`）：

1. **新出场角色**——有名字的，姓名 ＋ 身份 ＋ 与主角关系一句话。
2. **新埋设 / 推进 / 回收伏笔**——伏笔编号 ＋ 动作 ＋ 落在正文哪一句。
3. **资源变更**——获取 / 消耗 / 损毁，散文列举。
4. **本章状态变化点**——散文列举「谁的什么，从什么变成了什么」
   （例：「某物的持有者：母亲 → 主角」「主角所在地：矿下棚户 → 沟里被堵住」）。
   **不要求填 7 列表格**：规范化由本地 Agent 完成，你只需说清"发生了什么变化"。

{leak.OUTPUT_FORMAT_GUARD}
- 模板示例名称仅为格式示范、非本书预设设定，禁止照抄。
"""


def _outline_task(beat: Optional[dict]) -> str:
    hook = beat.get("钩子类型", "") if beat else ""
    kind = beat.get("核心事件类型", "") if beat else ""
    lines = ["按【必读模板】的单章细纲模板**全字段**产出本章细纲，逐项落地【已有数据】A 的节拍：", ""]
    lines += [
        "1. 场景切分 2~5 场，每场写明**预算字数 / 功能（推进·转折·情感·揭示）/ 内容要点 / 场景钩子**；"
        "四类功能不重复，禁止连续两个「转折」场；单场同质内容不超过 800 字。",
        "2. 「## 出场对象」表**必填且完整**——本章登场、被提及并影响本章、或状态被改动的对象逐个列出，"
        "含物品 / 财务 / 关系类状态对象。此表是章后状态对账的取数依据，漏一个就会在对账时报警。",
        "3. 涉及数值 / 计量 / 经济的机制必须**在细纲内闭合**（单位、折算、克扣基数与结果算得通），"
        "不得留给正文临场编数字。",
        "4. 伏笔的埋设 / 推进 / 回收只能用【已有数据】里已登记的编号，**禁止现编**；本章不涉及就写「无」。",
    ]
    if kind:
        lines.append(f"5. 本章核心事件类型为「{kind}」，须按【必读模板】对应的事件卡模板补齐要素区块。")
    if hook:
        lines.append(f"6. 章末钩子类型为「{hook}」，按此设计，不得改成别的强度。")
    lines.append("7. 拿不准、或【已有数据】不足以判定的，列进「待确认清单」交作者裁决，**不要自行编造**。")
    return "\n".join(lines) + "\n"


def _outline_output_format() -> str:
    return f"""严格按【必读模板】单章细纲模板的区块与字段顺序输出，一个区块都不省略。

- `@引用` 名称必须与【已有数据】里的定稿名称完全一致；对象确实不存在时，
  按实体创建规范当场建卡并附【随文新设实体卡片】，仅「登场在更晚卷」或「宜由专属任务设计的承重对象」
  才登记前向引用 TODO（须填「预计引入卷」）。
- 关系对象 ID 两端按 Unicode 码位排序拼接、分隔符恰一个 ASCII `&`。
- 模板中出现的示例名称仅为格式示范，禁止照抄。

{leak.OUTPUT_FORMAT_GUARD}
"""


def _outline_selfcheck() -> str:
    return """- [ ] 模板全字段齐全，无省略区块
- [ ] 场景功能不重复；无连续两个「转折」场；单场同质内容 ≤800 字
- [ ] 各场预算字数之和落在本章字数口径内
- [ ] 「## 出场对象」表完整（含 物品 / 财务 / 关系 类状态对象）
- [ ] 数值 / 计量 / 经济机制在细纲内闭合，正文无需临场编数字
- [ ] 伏笔编号全部来自【已有数据】，无现编
- [ ] 每个 `@引用` 的名称与【已有数据】一致
- [ ] 与【已有数据】A 的节拍摘要逐项对得上，没有多出摘要之外的主线事件
- [ ] 不确定处已列入「待确认清单」，没有自行编造
"""


def _sliding_window(ctx: Ctx, todos: list[str]) -> str:
    L = ctx.layout
    if L.chapter <= 1:
        return "首章无前置上下文。\n"
    prev = resolve_prev_manuscript(ctx)
    if prev is None or not prev.exists():
        return _todo(todos, "上一章正文尚未落位，滑动窗口留空",
                     "先把上一章正文落位到 `10_正文/…`，再重拼本提示词")
    tail = extract.tail_text(prev.read_text(encoding="utf-8", errors="ignore"))
    return ("### 上章结尾原文（最后 500 字，正文须紧密承接其场景与语气）\n\n"
            "```\n" + tail + "\n```\n\n"
            "### 上章末尾摘要\n\n"
            + _todo(todos, "上章 300 字摘要需人工撰写",
                    "摘要是理解性压缩，工具不代笔；写完粘到本区块"))


def resolve_prev_manuscript(ctx: Ctx) -> Optional[Path]:
    L = ctx.layout
    cand = L.manuscript.parent / f"章{L.chapter - 1:04d}.md"
    return cand if cand.exists() else None
