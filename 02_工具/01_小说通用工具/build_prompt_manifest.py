#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
任务输入清单 · 人读视图派生 (build_prompt_manifest.py)

机读权威是 `00_通用模板/04_提示词/任务输入清单.toml`（`prompt_build/manifest.py` 读它，
`assemble.py` 按它拼装）。本脚本把它渲染成人读表格 `00_通用模板/04_提示词/任务输入清单.md`
——`00_系统架构规范.md` §二·A 第 2 类「脚本派生的只读视图」。

用法
----
    build_prompt_manifest.py [仓库根] [--write]

    默认只**检查**：重出结果与磁盘上的 `.md` 逐字比对，不一致返回 1。
    --write   重新生成并写盘。

`audit_rules.py` 的 `RULE009` 在 check.sh 里跑同一套比对（连同 source 解析 / 节名存在校验）。
"""
import argparse
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_build import manifest  # noqa: E402

VIEW_REL = "00_通用模板/04_提示词/任务输入清单.md"


def _source_label(step: manifest.Step) -> str:
    k, r = step.kind, step.ref
    if k == "tpl":
        return f"模板 `{r}`"
    if k == "data":
        return f"数据 `{r}`"
    if k == "layout":
        return {"outline": "本章细纲", "opener_state": "本章开篇状态",
                "protagonist": "主角档案", "volume_plan": "本卷大纲"}.get(r, r)
    if k == "resolver":
        return f"计算 `{r}`"
    if k == "authored":
        return f"本工具撰写 `{r}`"
    return step.source


def _mode_label(step: manifest.Step) -> str:
    if step.mode == "whole":
        return "整份"
    if step.mode == "sections":
        return "取节：" + " / ".join(step.sections)
    if step.mode == "fields":
        return "取字段：" + " / ".join(step.fields)
    return step.mode


def render(man: manifest.Manifest) -> str:
    head = [
        "# 任务输入清单（人读视图）",
        "",
        "> **派生 · 禁止手工编辑 · 非权威**",
        "> 由 `02_工具/01_小说通用工具/build_prompt_manifest.py --write` 从",
        "> `00_通用模板/04_提示词/任务输入清单.toml`（机读权威）重出。改「某任务要哪些输入、",
        "> 每个输入整份还是取哪几节」请改那份 `.toml`，然后重跑本脚本；直接改本文件会被",
        "> `audit_rules.py` 的 `RULE009` 拦下。",
        "",
        "> 「什么任务需要什么信息」此前散在 `00_使用说明.md` 路由表、`00_云端提示词生成器.md`",
        "> 与 `assemble.py` 三处、已经漂移过。现在这三处对脚本化任务（细纲 / 正文）都指向上面那份 `.toml`。",
        "",
        "---",
        "",
    ]
    body: list[str] = []
    for name, task in man.tasks.items():
        body.append(f"## 任务：{name}")
        body.append("")
        body.append(f"- 说明：{task.description}")
        body.append(f"- 脚本：`{task.scripted}`（`prompt_build/assemble.py`）")
        body.append(f"- 骨架段：{' → '.join(f'【{s}】' for s in task.segments)}")
        body.append("")
        body.append("| 骨架段 | 输入 | 内联方式 | 区块标题 / 说明 |")
        body.append("|---|---|---|---|")
        for st in task.steps:
            when = "" if st.when == "always" else f"（{st.when}）"
            desc = st.title or st.note or "—"
            if st.title and st.note:
                desc = f"{st.title}；{st.note}"
            body.append(f"| 【{st.into}】{when} | {_source_label(st)} | {_mode_label(st)} | {desc} |")
        body.append("")
    return "\n".join(head + body).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="从任务输入清单 TOML 派生人读视图")
    ap.add_argument("repo_root", nargs="?", default=None)
    ap.add_argument("--check", action="store_true", help="（默认）只检查，不写盘")
    ap.add_argument("--write", action="store_true", help="重新生成并写盘")
    args = ap.parse_args()

    repo_root = (Path(args.repo_root).resolve() if args.repo_root
                 else Path(__file__).resolve().parents[2])

    try:
        man = manifest.load(repo_root)
    except manifest.ManifestError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2

    want = render(man)
    out = repo_root / VIEW_REL
    have = out.read_text(encoding="utf-8") if out.exists() else ""

    if have == want:
        print(f"  ✔ {VIEW_REL}（与 TOML 一致）")
        return 0

    if args.write:
        out.write_text(want, encoding="utf-8")
        print(f"  ↻ {VIEW_REL} 已重出")
        return 0

    diff = list(difflib.unified_diff(have.splitlines(), want.splitlines(),
                                     "磁盘上的视图", "按 TOML 重出", lineterm="", n=0))
    print(f"  ✘ {VIEW_REL} 与 TOML 不一致（{len(diff)} 行差异）", file=sys.stderr)
    for l in diff[:20]:
        print(f"      {l}", file=sys.stderr)
    print("\n跑 `build_prompt_manifest.py --write` 重出。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
