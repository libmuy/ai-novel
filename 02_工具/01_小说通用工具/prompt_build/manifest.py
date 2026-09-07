# -*- coding: utf-8 -*-
"""
任务输入清单加载 (manifest.py)

读 `00_通用模板/04_提示词/任务输入清单.toml`——「某任务的提示词该内联哪些源、
每个源整份还是取哪几节」的唯一机读权威（`00_系统架构规范.md` §二·A）。

`assemble.py` 按本清单拼装；`build_prompt_manifest.py` 从它派生人读视图；
`audit_rules.py` 的 `RULE009` 校验它。本模块只做「读 + 结构化」，不做取材。
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_REL = "00_通用模板/04_提示词/任务输入清单.toml"

# resolver / authored 名字白名单——RULE009 与 assemble.py 共用这一份
RESOLVERS = {
    "redline_fallback", "sysinst_common", "event_templates_outline",
    "event_templates_beat", "cast_cards_outline", "cast_cards_beat",
    "wr_hard_manuscript", "wr_hard_outline", "dy_fh_outline", "dy_fh_beat",
    "task_directive", "scene_budget", "opening_contract", "beat_block",
    "sliding_window", "opener_state_outline",
    "volume_resource_plan", "cultivation_breakthrough_outline",
}
AUTHORED = {
    "role_manuscript", "role_outline", "no_invent", "output_format_manuscript",
    "noinvent_selfcheck", "outline_task", "outline_output_format", "outline_selfcheck",
}
LAYOUT_SOURCES = {"outline", "opener_state", "protagonist", "volume_plan"}
SOURCE_KINDS = {"tpl", "data", "layout", "resolver", "authored"}
MODES = {"whole", "sections", "fields", "resolver", "authored"}
WHENS = {"always", "opening", "has_redline", "no_redline"}


class ManifestError(Exception):
    pass


@dataclass
class Step:
    into: str
    source: str                 # 原始 "kind:ref"
    mode: str = "whole"
    title: str = ""
    role: str = "data"
    when: str = "always"
    sections: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def kind(self) -> str:
        return self.source.split(":", 1)[0]

    @property
    def ref(self) -> str:
        return self.source.split(":", 1)[1] if ":" in self.source else ""


@dataclass
class Task:
    name: str
    description: str
    scripted: str
    segments: list[str]
    lettered: set[str]
    steps: list[Step]


@dataclass
class Manifest:
    tasks: dict[str, Task]

    def task(self, name: str) -> Task:
        if name not in self.tasks:
            raise ManifestError(f"任务输入清单里没有任务「{name}」，有的是 {sorted(self.tasks)}")
        return self.tasks[name]


def _parse_step(raw: dict, task_name: str) -> Step:
    try:
        step = Step(
            into=raw["into"],
            source=raw["source"],
            mode=raw.get("mode", "whole"),
            title=raw.get("title", ""),
            role=raw.get("role", "data"),
            when=raw.get("when", "always"),
            sections=list(raw.get("sections", [])),
            fields=list(raw.get("fields", [])),
            note=raw.get("note", ""),
        )
    except KeyError as e:
        raise ManifestError(f"[{task_name}] step 缺字段 {e}：{raw}") from e
    if step.kind not in SOURCE_KINDS:
        raise ManifestError(f"[{task_name}] 未知 source 前缀「{step.kind}」：{step.source}")
    if step.mode not in MODES:
        raise ManifestError(f"[{task_name}] 未知 mode「{step.mode}」")
    if step.when not in WHENS:
        raise ManifestError(f"[{task_name}] 未知 when「{step.when}」")
    if step.kind == "resolver" and step.ref not in RESOLVERS:
        raise ManifestError(f"[{task_name}] 未登记的 resolver「{step.ref}」（白名单在 manifest.py）")
    if step.kind == "authored" and step.ref not in AUTHORED:
        raise ManifestError(f"[{task_name}] 未登记的 authored「{step.ref}」")
    if step.kind == "layout" and step.ref not in LAYOUT_SOURCES:
        raise ManifestError(f"[{task_name}] 未知 layout source「{step.ref}」")
    if step.mode == "sections" and not step.sections:
        raise ManifestError(f"[{task_name}] mode=sections 但 sections 为空：{step.source}")
    if step.mode == "fields" and not step.fields:
        raise ManifestError(f"[{task_name}] mode=fields 但 fields 为空：{step.source}")
    return step


def parse(text: str) -> Manifest:
    data = tomllib.loads(text)
    tasks: dict[str, Task] = {}
    for name, tconf in data.get("task", {}).items():
        segments = list(tconf.get("segments", []))
        steps = [_parse_step(s, name) for s in tconf.get("step", [])]
        for st in steps:
            if st.into not in segments:
                raise ManifestError(f"[{name}] step.into「{st.into}」不在 segments {segments} 里")
        tasks[name] = Task(
            name=name,
            description=tconf.get("description", ""),
            scripted=tconf.get("scripted", ""),
            segments=segments,
            lettered=set(tconf.get("lettered", [])),
            steps=steps,
        )
    if not tasks:
        raise ManifestError("任务输入清单里一个 [task.*] 都没有")
    return Manifest(tasks)


def load(repo_root: Path) -> Manifest:
    p = repo_root / MANIFEST_REL
    if not p.is_file():
        raise ManifestError(f"任务输入清单不存在：{MANIFEST_REL}")
    return parse(p.read_text(encoding="utf-8"))
