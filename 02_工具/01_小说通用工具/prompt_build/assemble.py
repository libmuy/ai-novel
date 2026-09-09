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
import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import extract, leak, manifest
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


_RE_OUTLINE_ORIGIN = re.compile(r"规划_卷\d+_章\d+\.md$")


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

    def source_leaks(self) -> list[leak.Leak]:
        """扫**逐字内联的细纲**里的内部标识。

        规则第 6 条点名【已有数据】也在禁区内，而细纲整段内联在【已有数据】里、
        又是模型转写正文的主要依据——ch0003 首版正文出现「章0001」，源头就在这里。
        本工具不改写源文件（那是作者的数据），只把命中项报出来让人去改源文件。
        """
        if not self.prose_output:
            return []
        out: list[leak.Leak] = []
        for sec in self.sections:
            for b in sec.blocks:
                # 按**来源路径**认，不按标题关键字——【必读规则】里还内联着
                # 「单章细纲字段说明」（模板本身），它的示例值带章号是正常的。
                origin = b.origin or ""
                if b.authored or origin.startswith("extract:") \
                        or not _RE_OUTLINE_ORIGIN.search(origin):
                    continue
                out += leak.scan_source(f"{sec.title} · {b.title}", b.body)
        return out

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


def _strip_template_framing(block: str) -> str:
    """剥掉 `01_系统指令.md` 任务块自带的示例抬头。

    模板里每个任务块形如：

        【任务】写第X章正文
        ## 执行要求
        1. ...

    「第X章」是占位符、「## 执行要求」与本脚本自己发的小节标题重复，两行都不该进产出。
    """
    lines = block.lstrip("\n").split("\n")
    while lines and (
        re.match(r"^【任务】.*第X章", lines[0].strip())
        or lines[0].strip() == "## 执行要求"
    ):
        lines.pop(0)
    return "\n".join(lines).strip("\n")


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


# ─────────────────────────────────────────────────────────── 清单驱动拼装
#
# 「某任务的提示词该内联哪些源、每个源整份还是取哪几节」的权威是
# `00_通用模板/04_提示词/任务输入清单.toml`（`manifest.py` 读它）。下面的 build_* 只是
# 按清单逐 step 分派：tpl/data/layout 直接取材，resolver 调对应函数（动态计算逻辑留这里），
# authored 是本工具撰写的固定文本。改内联粒度＝改那份 TOML，不改本文件。


def _outline_text(ctx: Ctx, cache: dict) -> str:
    if "outline_text" not in cache:
        cache["outline_text"] = ctx.read_path(ctx.layout.outline)
    return cache["outline_text"]


def _sysinst_text(ctx: Ctx, cache: dict) -> str:
    if "sysinst" not in cache:
        cache["sysinst"] = ctx.tpl("01_写作规则/01_系统指令.md")
    return cache["sysinst"]


def _beat(ctx: Ctx, cache: dict):
    if "beat" not in cache:
        cache["beat"] = _beat_row(ctx.read_path(ctx.layout.volume_plan), ctx.layout.chapter)
    return cache["beat"]


def _has_redline(ctx: Ctx) -> bool:
    p = ctx.novel_dir / "01_设定/00_红线包.md"
    try:
        return p.is_file() and bool(p.read_text(encoding="utf-8", errors="ignore").strip())
    except OSError:
        return False


def _when_ok(ctx: Ctx, step: manifest.Step) -> bool:
    if step.when == "opening":
        return 1 <= ctx.layout.chapter <= 3
    if step.when == "has_redline":
        return _has_redline(ctx)
    if step.when == "no_redline":
        return not _has_redline(ctx)
    return True


def _apply_mode(text: str, step: manifest.Step) -> str:
    if not text:
        return text
    if step.mode == "sections":
        return extract.read_sections(text, step.sections)
    if step.mode == "fields":
        return extract.card_fields(text, step.fields)
    return text


def _blocks_of(sec: Section) -> list[tuple[str, str, str]]:
    """把一个临时 Section 的 block 摊平成 (title, body, origin) —— 供 resolver 复用
    直接写 Section 的既有辅助函数（_add_cast_cards / _add_dy_and_fh）。"""
    return [(b.title, b.body, b.origin) for b in sec.blocks]


# ── authored：本工具撰写的固定文本，返回 (body, origin) ──

def _au_role_manuscript(ctx, cache):
    return ROLE_MANUSCRIPT.format(novel=ctx.novel_name), "authored"


def _au_role_outline(ctx, cache):
    return ROLE_OUTLINE.format(novel=ctx.novel_name), "authored"


def _au_no_invent(ctx, cache):
    # 逐字取自「示例去污染规则」第 6 条与「与正文阶段任务的关系」§6，非本工具撰写
    return NO_INVENT, f"{TEMPLATES}/04_提示词/00_云端提示词生成器.md"


def _au_output_format_manuscript(ctx, cache):
    return _output_format(), "authored"


def _au_noinvent_selfcheck(ctx, cache):
    return "逐句检查——本段是否引入了细纲没有的东西？若有，删除或退回细纲层。\n", "authored"


def _au_outline_task(ctx, cache):
    return _outline_task(_beat(ctx, cache)), "authored"


def _au_outline_output_format(ctx, cache):
    return _outline_output_format(), "authored"


def _au_outline_selfcheck(ctx, cache):
    return _outline_selfcheck(), "authored"


_AUTHORED = {
    "role_manuscript": _au_role_manuscript,
    "role_outline": _au_role_outline,
    "no_invent": _au_no_invent,
    "output_format_manuscript": _au_output_format_manuscript,
    "noinvent_selfcheck": _au_noinvent_selfcheck,
    "outline_task": _au_outline_task,
    "outline_output_format": _au_outline_output_format,
    "outline_selfcheck": _au_outline_selfcheck,
}


# ── resolver：动态计算，返回 [(title, body, origin), …] ──

def _rv_redline_fallback(ctx, step, todos, cache):
    ctx.data("01_设定/00_红线包.md")  # 触发缺失记录，与旧行为一致
    todos.append("本书尚无 `01_设定/00_红线包.md`，已退回「逐份摘抄」老做法，建议补建")
    out = [("通用写作规则（生成版）", ctx.tpl("01_写作规则/00_通用写作规则_生成版.md"),
            f"{TEMPLATES}/01_写作规则/00_通用写作规则_生成版.md")]
    out.append(("本书文风差异", ctx.data("01_设定/00_文风.md"), "01_设定/00_文风.md"))
    return out


def _rv_sysinst_common(ctx, step, todos, cache):
    sysinst = _sysinst_text(ctx, cache)
    common = extract.read_section(sysinst, "通用指令（所有任务共用）")
    for h in ("核心原则", "文风红线", "世界基本法则红线", "版权与人设红线"):
        common += "\n" + extract.read_section(sysinst, h)
    return [(step.title, common, f"{TEMPLATES}/01_写作规则/01_系统指令.md")]


def _rv_event_templates_outline(ctx, step, todos, cache):
    return [(label, ctx.tpl(p), f"{TEMPLATES}/{p}")
            for label, p in _event_templates(_outline_text(ctx, cache))]


def _rv_event_templates_beat(ctx, step, todos, cache):
    return [(label, ctx.tpl(p), f"{TEMPLATES}/{p}")
            for label, p in _event_templates_from_beat(_beat(ctx, cache))]


def _rv_cast_cards_outline(ctx, step, todos, cache):
    tmp = Section("_")
    _add_cast_cards(ctx, tmp, _outline_text(ctx, cache),
                    sections=step.sections, todos=todos)
    return _blocks_of(tmp)


def _rv_cast_cards_beat(ctx, step, todos, cache):
    beat = _beat(ctx, cache)
    tmp = Section("_")
    _add_cast_cards(ctx, tmp, beat.get("摘要", "") if beat else "",
                    from_beat=True, sections=step.sections, todos=todos)
    return _blocks_of(tmp)


def _wr_hard_or_todo(ctx, todos):
    """硬规则行；有 WR 规则却一条「硬」都没取到 → 记 todo（多半是列错位），返回 []。"""
    concept = ctx.data("01_设定/00_小说概念.md")
    hard = extract.wr_rules(concept, ("硬",))
    if not hard and extract.wr_rules(concept, states=None):
        todos.append("【世界基本法则】有 WR 规则，但没有一条是「硬」状态——"
                     "状态列错位？硬规则清单这次是空的")
    return concept, hard


def _rv_wr_hard_manuscript(ctx, step, todos, cache):
    concept, hard = _wr_hard_or_todo(ctx, todos)
    if not hard:
        return []
    body = ("以下为世界**硬规则**，本章不得违反：\n\n"
            "| 规则ID | 名称 | 状态 | 内容 |\n|---|---|---|---|\n" + "\n".join(hard) + "\n\n"
            + extract.read_section(concept, "信息与认知法则"))
    return [(step.title, body, "extract:01_设定/00_小说概念.md")]


def _rv_wr_hard_outline(ctx, step, todos, cache):
    _concept, hard = _wr_hard_or_todo(ctx, todos)
    if not hard:
        return []
    body = "| 规则ID | 名称 | 状态 | 内容 |\n|---|---|---|---|\n" + "\n".join(hard) + "\n"
    return [(step.title, body, "extract:01_设定/00_小说概念.md")]


def _rv_dy_fh_outline(ctx, step, todos, cache):
    tmp = Section("_")
    _add_dy_and_fh(ctx, tmp, _outline_text(ctx, cache), todos=todos)
    return _blocks_of(tmp)


def _rv_dy_fh_beat(ctx, step, todos, cache):
    beat = _beat(ctx, cache)
    tmp = Section("_")
    _add_dy_and_fh(ctx, tmp, beat.get("摘要", "") if beat else "", todos=todos)
    return _blocks_of(tmp)


def _rv_task_directive(ctx, step, todos, cache):
    # 只取围栏内的「给云端的指令」；围栏后的「拼装为云端提示词时……」是给本地
    # Agent 的操作说明（含仓库路径与脚本名），不发给云端。
    task2 = extract.fenced_block(
        extract.read_section(_sysinst_text(ctx, cache), "任务2：写单章正文"))
    # 围栏内自带模板的示例抬头「【任务】写第X章正文」+「## 执行要求」。前者的「第X章」是
    # 占位符（照抄会把未替换的 X 发给云端），后者与本段自己的「## 执行要求」小节标题重复。
    # 两行都属于模板的自我框架，剥掉；真正的章次由本段标题与【已有数据】里的细纲交代。
    task2 = _strip_template_framing(task2)
    task2 = _retarget(task2, {
        # 「按 07_单章细纲模板 的场景列表写作」指的是**本章那份细纲**，不是模板的字段说明
        "07_单章细纲模板": "【已有数据】的本章细纲",
        # 「逐条检查 00_通用写作规则 第八章自检清单」→ 清单本体内联在【输出后自检】
        "00_通用写作规则": "【输出后自检】的单章一站式自检清单",
        "01_设定/00_小说概念.md": "【已有数据】的世界基本法则 · 硬规则清单",
        "00_通用模板/03_字段词表.md": "【输出格式】的【待登记清单】",
        "01_状态履历.md": "本章状态履历（本地 Agent 负责，云端不必关心）",
    })
    return [(step.title, task2, f"{TEMPLATES}/01_写作规则/01_系统指令.md")]


def _rv_scene_budget(ctx, step, todos, cache):
    return [(step.title, _scene_budget_table(_outline_text(ctx, cache), todos),
             f"extract:{rel(ctx.novel_dir, ctx.layout.outline)}")]


def _rv_opening_contract(ctx, step, todos, cache):
    guide = ctx.tpl("01_写作规则/05_开篇三章设计指南.md")
    contract = (extract.read_section(guide, "六、三章整体契约自检清单")
                or extract.read_section(guide, "六、开篇三章契约自检清单")
                or extract.read_section(guide, "六、契约自检清单"))
    if not contract:
        todos.append("开篇三章设计指南里找不到「六、…契约自检清单」小节——"
                     "标题改过？【输出后自检】少了这块")
        return []
    return [(step.title, contract, f"{TEMPLATES}/01_写作规则/05_开篇三章设计指南.md")]


def _rv_beat_block(ctx, step, todos, cache):
    return [(step.title, _beat_block(_beat(ctx, cache), todos),
             f"extract:{rel(ctx.novel_dir, ctx.layout.volume_plan)}")]


def _rv_sliding_window(ctx, step, todos, cache):
    return [(step.title, _sliding_window(ctx, todos), "extract:上一章正文")]


def _rv_opener_state_outline(ctx, step, todos, cache):
    L = ctx.layout
    body = ctx.read_path(L.opener_state)
    if not body:
        body = _todo(
            todos, "本章开篇状态未物化",
            "正常由 `build_prompt.py` 准备阶段调 "
            "`build_state_snapshot.py --chapter-opener` 物化；见到此占位说明当次是 "
            "--dry-run，或物化被阻断（前序章未跑 merge_chapter_state.py）")
    elif not body.lstrip().startswith("# 本章开篇状态"):
        # `--write-chapter-openers` 写的抬头恒为「# 本章开篇状态 · <章路径>」并带完整溯源注。
        # 抬头不对 → 多半是有人用 `--at-chapter` 手跑或手改了（ch0004 首版抬头是
        # 「# 开篇状态快照 · ../4」）。派生视图不该手搓。
        todos.append("本章 `00_开篇状态.md` 抬头不是「# 本章开篇状态 · <章路径>」——"
                     "多半是用 `--at-chapter` 手跑或手改的；请用 "
                     "`build_state_snapshot.py --write-chapter-openers` 重新物化")
    return [(step.title, body, rel(ctx.novel_dir, L.opener_state))]


def _rv_volume_resource_plan(ctx, step, todos, cache):
    """卷大纲【资源与伏笔规划】的资源 / 财务子表。

    细纲要在章内算清「本章特有的钱 / 数量 / 克扣」（瘦身闸的例外），需要锚点：
    某资源值多少下品、本阶段财富区间、续命丹价位……节拍表那一行不含这些，
    ch0004 首版提示词因此让规划师无处取数（灵心草 ≈5 下品在卷大纲子表里、没内联）。
    """
    plan = ctx.read_path(ctx.layout.volume_plan)
    body = extract.read_sections(plan, [
        "本卷新增资源/道具",
        "本卷损毁/失去资源",
        "本卷资源变化轨迹",
        "本卷财务危机安排",
    ])
    if not body.strip():
        return []
    lead = ("卷大纲【资源与伏笔规划】的资源 / 财务子表。**本章特有的钱 / 数量 / 克扣要在"
            "细纲内算清**（单位、折算率、基数、结果），源数字以下表与内联的资源卡为准；"
            "全书恒定的换算基准仍写指针「见红线包 §X」。\n\n")
    return [(step.title, lead + body,
             f"extract:{rel(ctx.novel_dir, ctx.layout.volume_plan)}")]


_MAIN_CULTIVATION_RE = re.compile(r"主修[：:]\s*([^\（(，,、；;。\n/]+)")


def _rv_cultivation_breakthrough_outline(ctx, step, todos, cache):
    """突破 / 晋升章：内联主角主修体系的【境界体系】与【破境考验】。

    突破卡模板的【冲突原型】要求「与修炼体系【大境界破境考验】对应」，但首次
    引气入体（凡人→炼气初期）按体系设计**没有破境考验**（玄元道炼气初期一行
    破境考验＝「—」）。不内联体系卡，规划师无从判断，只能硬凑戏剧化考验、
    反而和体系卡打架（ch0004 首版提示词的 #6）。
    """
    beat = _beat(ctx, cache)
    if not beat:
        return []
    hay = f"{beat.get('必用模板', '')} {beat.get('核心事件类型', '')}"
    if not any(k in hay for k in ("突破", "晋升")):
        return []
    prot_path = extract.card_path(ctx.novel_dir, extract.Ref("主角", ""))
    prot = ctx.read_path(prot_path) if prot_path else ""
    m = _MAIN_CULTIVATION_RE.search(prot)
    if not m:
        todos.append("突破章：主角档案里解析不到「主修：<体系>」——"
                     "无法内联修炼体系【境界体系】/【破境考验】，请手工补")
        return []
    system = m.group(1).strip().strip("《》")
    sys_path = ctx.novel_dir / "02_数据库/01_修炼体系" / f"01_修炼体系_{system}.md"
    if not sys_path.exists():
        todos.append(f"突破章：找不到主修体系卡 `01_修炼体系_{system}.md`——"
                     f"【境界体系】/【破境考验】未内联，请手工补")
        return []
    text = ctx.read_path(sys_path)
    body = extract.read_sections(text, ["【基础定义】", "【境界体系】", "【体系规则】"])
    if not body.strip():
        return []
    lead = (f"主角主修 **{system}**。本章为突破 / 晋升章——**冲突原型须与下表"
            "【破境考验】列对应**；某境界那一格是「—」的（如炼气初期＝引气入体），"
            "说明该阶段按体系设计没有正式破境考验，冲突原型写实际发生的身体 / 处境"
            "考验即可，不要硬凑一个体系外的「考验」。\n\n")
    title = step.title or f"本章突破 · 主修体系（{system}）境界与破境考验"
    return [(title, lead + body, f"extract:{rel(ctx.novel_dir, sys_path)}")]


_RESOLVERS = {
    "redline_fallback": _rv_redline_fallback,
    "sysinst_common": _rv_sysinst_common,
    "event_templates_outline": _rv_event_templates_outline,
    "event_templates_beat": _rv_event_templates_beat,
    "cast_cards_outline": _rv_cast_cards_outline,
    "cast_cards_beat": _rv_cast_cards_beat,
    "wr_hard_manuscript": _rv_wr_hard_manuscript,
    "wr_hard_outline": _rv_wr_hard_outline,
    "dy_fh_outline": _rv_dy_fh_outline,
    "dy_fh_beat": _rv_dy_fh_beat,
    "task_directive": _rv_task_directive,
    "scene_budget": _rv_scene_budget,
    "opening_contract": _rv_opening_contract,
    "beat_block": _rv_beat_block,
    "sliding_window": _rv_sliding_window,
    "opener_state_outline": _rv_opener_state_outline,
    "volume_resource_plan": _rv_volume_resource_plan,
    "cultivation_breakthrough_outline": _rv_cultivation_breakthrough_outline,
}


def _resolve_step(ctx: Ctx, step: manifest.Step, todos: list, cache: dict) -> list:
    k = step.kind
    if k == "tpl":
        return [(step.title, _apply_mode(ctx.tpl(step.ref), step), f"{TEMPLATES}/{step.ref}")]
    if k == "data":
        return [(step.title, _apply_mode(ctx.data(step.ref), step), step.ref)]
    if k == "layout":
        if step.ref == "outline":
            return [(step.title, _apply_mode(_outline_text(ctx, cache), step),
                     rel(ctx.novel_dir, ctx.layout.outline))]
        if step.ref == "opener_state":
            return [(step.title, _apply_mode(ctx.read_path(ctx.layout.opener_state), step),
                     rel(ctx.novel_dir, ctx.layout.opener_state))]
        if step.ref == "volume_plan":
            return [(step.title, _apply_mode(ctx.read_path(ctx.layout.volume_plan), step),
                     rel(ctx.novel_dir, ctx.layout.volume_plan))]
        if step.ref == "protagonist":
            p = extract.card_path(ctx.novel_dir, extract.Ref("主角", ""))
            if p is None:
                return []
            return [(step.title, _apply_mode(ctx.read_path(p), step), rel(ctx.novel_dir, p))]
        return []
    if k == "authored":
        body, origin = _AUTHORED[step.ref](ctx, cache)
        return [(step.title, body, origin)]
    if k == "resolver":
        return _RESOLVERS[step.ref](ctx, step, todos, cache)
    return []


def _build(ctx: Ctx, task_key: str, header: str, prose_output: bool) -> Prompt:
    spec = manifest.load(ctx.repo_root).task(task_key)
    secs = {name: Section(f"【{name}】", lettered=(name in spec.lettered))
            for name in spec.segments}
    todos: list[str] = []
    cache: dict = {}
    for step in spec.steps:
        if not _when_ok(ctx, step):
            continue
        for title, body, origin in _resolve_step(ctx, step, todos, cache):
            secs[step.into].add(title, body, origin)
    return Prompt(header, [secs[n] for n in spec.segments], todos, prose_output=prose_output)


# ─────────────────────────────────────────────────────────── 正文

def build_manuscript(ctx: Ctx) -> Prompt:
    L = ctx.layout
    header = _header(
        ctx, L.prompt_dir / "01_正文生成.md", L.output_dir / "01_正文生成.md",
        L.manuscript, f"写《{ctx.novel_name}》本章正文",
        "落位正文 → 按【输出格式】的【待登记清单】回填本章 `02_状态/01_状态履历.md` → "
        "`merge_chapter_state.py --chapter-dir <本章目录>` → `audit_consistency.py` 复查 → "
        "`review_manuscript.py --chapter-dir <本章目录>` 起冷读循环。")
    return _build(ctx, "正文", header, prose_output=True)


# ─────────────────────────────────────────────────────────── 单章细纲

def build_outline(ctx: Ctx) -> Prompt:
    L = ctx.layout
    header = _header(
        ctx, L.prompt_dir / "00_单章细纲.md", L.output_dir / "00_单章细纲.md",
        L.outline, f"写《{ctx.novel_name}》本章细纲",
        "落位细纲到规划层 → `audit_consistency.py` 复查 → "
        "`review_manuscript.py --chapter-dir <本章目录> --mode outline` 起冷读循环"
        "（细纲门禁比正文严：必改项必须清零才能去拼正文提示词）。")
    return _build(ctx, "细纲", header, prose_output=False)


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


# 每份细纲都有的标准字段区块——标题里的事件关键词（尤其【道义与感悟】里的「道义」）
# 不构成「本章需要该事件卡」的信号，扫描前先剔除。
_OUTLINE_STD_HEADINGS = {
    "基础信息", "出场对象", "出场对象一览", "场景列表", "章级钩子",
    "道义与感悟", "自检预检", "本章突破卡", "待确认清单", "自检确认",
    "已裁决事项", "0. 上下文滑动窗口",
}
_SCENE_HEADING_RE = re.compile(r"^(第\s*\d+\s*场景|上章|与正文衔接)")


def _norm_heading(t: str) -> str:
    return t.strip().strip("【】").strip()


def _event_templates(outline_text: str) -> list[tuple[str, str]]:
    """细纲需要哪个事件卡模板 → 追加。

    优先看细纲【基础信息】的「核心事件类型 / 必用模板」（与节拍表口径一致）；
    取不到（旧格式细纲）才退回扫非标准区块标题——**标准细纲字段一律剔除**，
    否则每份细纲都有的【道义与感悟】会把「事件与感悟卡模板」误拉进来。
    """
    node = extract.field_value(outline_text, "关联卷大纲节点")
    m_kind = re.search(r"核心事件类型[：:]\s*([^/｜|]+)", node)
    m_tpl = re.search(r"必用模板[：:]\s*([^/｜|]+)", node)
    if m_kind or m_tpl:
        hay = f"{m_tpl.group(1) if m_tpl else ''} {m_kind.group(1) if m_kind else ''}"
        for key, (label, path) in _EVENT_TPL.items():
            if key in hay:
                return [(label, path)]
        return []

    out, seen = [], set()
    titles = " ".join(t for t in extract.section_titles(outline_text, max_level=3)
                      if _norm_heading(t) not in _OUTLINE_STD_HEADINGS
                      and not _SCENE_HEADING_RE.match(t.strip()))
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


def _add_cast_cards(ctx: Ctx, sec: Section, source_text: str, from_beat: bool = False,
                    sections: Optional[list[str]] = None, todos: Optional[list] = None):
    """把出场对象逐个解析成卡片内联。`sections` 非空时只取卡里那几个区块（原文逐字），
    为空 / None 时整份内联。

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
            cards.append((e.ref.name, e.ref.ref_type, p))

    sec.add(roster_title, lead + "\n".join(roster) + "\n", "extract:出场对象")
    for name, rtype, p in cards:
        text = ctx.read_path(p)
        # sections 收窄只对人物卡生效——势力/地理卡结构不同（无【角色内核】等），
        # 套人物 section 名会切出空串、整卡消失（枯港矿城/灰壤凡域曾这样被吞掉）。
        if sections and rtype == "人物":
            present = extract.sections_present(text, sections)
            if present:
                text = extract.read_sections(text, sections)
                missing = [s for s in sections if s not in present]
                if missing and todos is not None:
                    # 部分区块被改名 / 漏填 → 那几段静默消失过；现在报出来
                    todos.append(f"人物卡 `{p.name}` 缺清单声明的区块 {'、'.join(missing)}"
                                 f"——这几段未内联，检查是否按 `04_人物模板` 的区块结构填写")
            elif todos is not None:
                # 这张卡一个声明的区块都没有 → 结构不符预期，整卡照给、不丢数据
                todos.append(f"人物卡 `{p.name}` 没有清单声明的任何区块（{'/'.join(sections)}），"
                             f"已整份内联——检查卡片是否按 `04_人物模板` 的区块结构填写")
        sec.add(f"出场对象卡 · {name}", text, rel(ctx.novel_dir, p))


def _add_dy_and_fh(ctx: Ctx, sec: Section, source_text: str, todos: Optional[list] = None):
    refs = extract.parse_refs(source_text)
    dy_ids = [r.name for r in refs if r.ref_type == "道义"]
    fh_ids = [r.name for r in refs if r.ref_type == "伏笔"]

    # 细纲【道义与感悟】表里的「本章落地道义」也算
    m = re.search(r"DY-\d+", extract.read_section(source_text, "【道义与感悟】") or "")
    if m and m.group(0) not in dy_ids:
        dy_ids.append(m.group(0))

    if dy_ids:
        core = ctx.data("01_设定/05_核心道义.md")
        resolved = {d: extract.dy_block(core, d) for d in dy_ids}
        body = "\n\n".join(filter(None, resolved.values()))
        sec.add(f"本章道义 · {'、'.join(dy_ids)}", body, "extract:01_设定/05_核心道义.md")
        miss = [d for d, v in resolved.items() if not v.strip()]
        if miss and todos is not None:
            todos.append(f"细纲点名了道义 {'、'.join(miss)}，但 `01_设定/05_核心道义.md` 里"
                         f"取不到对应小节（标题需含该 ID）——这几条未内联")

    if fh_ids:
        ledger = ctx.data("03_规划/00_伏笔总纲.md")
        vol = ctx.read_path(ctx.layout.volume_foreshadow)
        body = _fh_registry_block(
            fh_ids,
            extract.ledger_rows(ledger, fh_ids),
            extract.ledger_rows(vol, fh_ids))
        if body:
            sec.add(f"本章伏笔 · {'、'.join(fh_ids)}", body,
                    "extract:03_规划/00_伏笔总纲.md")
        elif todos is not None:
            todos.append(f"细纲点名了伏笔 {'、'.join(fh_ids)}，但伏笔总纲 / 卷伏笔册里"
                         f"没有一条对应登记行——伏笔号写错？总纲漏登记？")


def _fh_registry_block(fh_ids: list[str], ledger_rows: list[str],
                       vol_rows: list[str]) -> str:
    """本章点名伏笔的登记行，**按来源分组**呈现。

    同一个 FH 号在「伏笔总纲」（全书权威）与「卷伏笔册」（埋设表 / 回收表）里往往
    各有一行，措辞与状态列不一定一致——例如总纲记「活跃」、卷册回收表记
    「已回收（阶段性）」。这是阶段性回收的**正常表现**（见 `foreshadow_schedule.py`
    说明），不是矛盾。旧实现把这些异构行裸拼成一张无表头的表，云端会分不清本章
    到底是推进还是回收（ch0004 首版提示词栽在这里）。现在分来源列出并写明口径。
    """
    if not (ledger_rows or vol_rows):
        return ""

    def _by_id(rows: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in rows:
            key = r.strip().strip("|").split("|")[0].strip().strip("*")
            out.setdefault(key, []).append(r)
        return out

    lg, vl = _by_id(ledger_rows), _by_id(vol_rows)
    lines = [
        "**禁止现编伏笔号。** 下列为本章点名伏笔的登记行，按来源分组，供你核对"
        "伏笔号 / 名称 / 既有含义：",
        "",
        "- 同一伏笔在**伏笔总纲**（全书权威）与**卷伏笔册**（埋设表 / 回收表）里可能各有登记，"
        "状态列不一定一致（如总纲「活跃」、卷册「已回收（阶段性）」）——这是阶段性回收的"
        "正常表现，不是矛盾。",
        "- **本章对某伏笔到底做什么（埋设 / 推进 / 回收）以【已有数据】A 的节拍摘要为准**，"
        "登记行不作数。",
        "",
    ]
    for fid in fh_ids:
        if fid not in lg and fid not in vl:
            continue
        lines.append(f"**{fid}**")
        for r in lg.get(fid, []):
            lines.append(f"- 〔伏笔总纲〕{r}")
        for r in vl.get(fid, []):
            lines.append(f"- 〔卷伏笔册〕{r}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


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
        seg = title + " " + body[:400]
        # 两种写法都要认：散文式「功能：铺垫」与表格行「| 场景功能 | 铺垫 |」
        m2 = (re.search(r"功能[：:]\s*([^\n，,）)]+)", seg)
              or re.search(r"\|\s*场景功能\s*\|\s*([^|\n]+?)\s*\|", seg))
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
        "3. **本章特有**的数值 / 计量 / 经济**变化**必须在细纲内算清（单位、折算率、基数、结果算得通），"
        "不得留给正文临场编数字；但全书恒定的换算基准（如「N 两废料折一枚」）、主角人设红线、异宝档位、"
        "禁用词等**写指针「见红线包 §X」，不重抄正文**——正文提示词会另行内联红线包，抄两遍只会口径漂移。",
        "4. 细纲是场景骨架、不是迷你提示词：场景「内容简述」≤120 字、只写本场事件；场景下的红线提醒用一句指针，不复述规则原文。",
        "5. 伏笔的埋设 / 推进 / 回收只能用【已有数据】里已登记的编号，**禁止现编**；本章不涉及就写「无」。",
    ]
    if kind:
        lines.append(f"6. 本章核心事件类型为「{kind}」，须按【必读模板】对应的事件卡模板补齐要素区块。")
    if hook:
        lines.append(f"7. 章末钩子类型为「{hook}」，按此设计，不得改成别的强度。")
    lines.append("8. 拿不准、或【已有数据】不足以判定的，列进「待确认清单」交作者裁决，**不要自行编造**。")
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
- [ ] 本章特有的数值 / 计量 / 经济变化在细纲内算清；全书基准与红线用指针「见红线包 §X」、未重抄
- [ ] 场景「内容简述」≤120 字；场景红线提醒是指针不是规则原文复述
- [ ] 伏笔编号全部来自【已有数据】，无现编
- [ ] 每个 `@引用` 的名称与【已有数据】一致
- [ ] 与【已有数据】A 的节拍摘要逐项对得上，没有多出摘要之外的主线事件
- [ ] 不确定处已列入「待确认清单」，没有自行编造
"""


# 上章摘要存 `00_提示词/上章摘要.md`（无数字前缀：辅助输入，不参与 WS006 产出配对）。
# 生成 = LLM 压缩，由 build_prompt.py 的准备阶段负责（见该脚本 _prepare）；本模块只读。
SUMMARY_FILENAME = "上章摘要.md"
_SUMMARY_LLM_MARKER = "<!-- 上章摘要 · LLM 生成待人工复核"


def read_prev_summary(digest: Path):
    """读 上章摘要.md。返回：

    - None                      文件缺失 / 空 / 仍是 `>>>` 占位
    - (正文, unreviewed: bool)   unreviewed=True 表示首行是 LLM 出处标记、尚未人工复核
    """
    try:
        raw = digest.read_text(encoding="utf-8") if digest.exists() else ""
    except OSError:
        raw = ""
    if not raw.strip() or raw.lstrip().startswith(">>>"):
        return None
    lines = raw.splitlines()
    if lines and lines[0].strip().startswith(_SUMMARY_LLM_MARKER):
        return "\n".join(lines[1:]).strip(), True
    return raw.strip(), False


def write_prev_summary(digest: Path, text: str) -> None:
    """写 LLM 生成的上章摘要，首行带出处标记（人工复核并接受后删掉首行即转为人工版）。"""
    digest.parent.mkdir(parents=True, exist_ok=True)
    marker = (f"{_SUMMARY_LLM_MARKER} · {datetime.date.today().isoformat()} · "
              "复核衔接、接受后删除此行 -->")
    digest.write_text(marker + "\n" + text.strip() + "\n", encoding="utf-8")


def _sliding_window(ctx: Ctx, todos: list[str]) -> str:
    L = ctx.layout
    if L.chapter <= 1:
        return "首章无前置上下文。\n"
    prev = resolve_prev_manuscript(ctx)
    if prev is None or not prev.exists():
        return _todo(todos, "上一章正文尚未落位，滑动窗口留空",
                     "先把上一章正文落位到 `10_正文/…`，再重拼本提示词")
    prev_text = prev.read_text(encoding="utf-8", errors="ignore")
    tail = extract.tail_text(prev_text)
    head = ("### 上章结尾原文（最后 500 字，正文须紧密承接其场景与语气）\n\n"
            "```\n" + tail + "\n```\n\n### 上章末尾摘要\n\n")
    digest = L.prompt_dir / SUMMARY_FILENAME
    got = read_prev_summary(digest)
    if got is None:
        return head + _todo(
            todos, "上章摘要缺失",
            f"正常由 `build_prompt.py` 准备阶段调 LLM 生成写入 `{rel(ctx.novel_dir, digest)}`；"
            "见到此占位说明当次是 --dry-run，或摘要生成被阻断（LLM 不可用）")
    body, unreviewed = got
    if unreviewed:
        todos.append(
            f"上章摘要为 LLM 生成、未经人工复核——冷读时须校对与上一章的衔接，"
            f"确认后删掉 `{rel(ctx.novel_dir, digest)}` 首行的出处标记")
    return head + body + "\n"


def resolve_prev_manuscript(ctx: Ctx) -> Optional[Path]:
    L = ctx.layout
    cand = L.manuscript.parent / f"章{L.chapter - 1:04d}.md"
    return cand if cand.exists() else None
