#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端提示词拼装 (build_prompt.py)

把「按六段骨架、全文内联模板与定稿数据、预建三类空文件」这套已经写得很精确的规格，
从每章手抄 141 KB 变成一条命令。

规格的唯一权威来源是 `00_通用模板/04_提示词/00_云端提示词生成器.md`
（骨架 / 预建 / 存档命名 / 规则引用纪律 / 示例去污染）与
`00_通用模板/01_写作规则/01_系统指令.md`（正文阶段任务指令）。本脚本只是它们的
可执行实现——规格变了改那两个文件，然后回来改本脚本，不要反过来。

用法
----
    build_prompt.py --novel 01_小说数据/00_苍玄 --task 正文   --chapter 3
    build_prompt.py --novel 01_小说数据/00_苍玄 --task 细纲   --chapter 3 [--part 1 --volume 1]
    build_prompt.py --chapter-dir 01_小说数据/00_苍玄/05_工作区/03_第01部/03_卷01/05_章0003 --task 正文

    --dry-run     只报告，不写任何文件
    --force       覆盖已存在的提示词存档（默认只创建、不覆盖）
    --no-prebuild 不预建回填 / 目标空文件

退出码
------
    0 = 已生成    1 = 用法/读取错误    2 = 前置门禁未过（已输出阻断报告，未写任何文件）
    3 = 生成了但内部标识自检未过（提示词已写出，需人工过一遍）
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prompt_build import assemble, layout as L, leak, progress  # noqa: E402

TASKS = {
    "正文": ("正文生成", "01_正文生成.md"),
    "细纲": ("单章细纲", "00_单章细纲.md"),
}


def _gate(ctx: assemble.Ctx, task: str) -> list[progress.Blocker]:
    """前置门禁。`00_使用说明.md`【前置阻断】：必读数据必须全部为「定稿」。"""
    idx = progress.ProgressIndex(ctx.novel_dir)
    lay = ctx.layout
    blockers: list[progress.Blocker] = []

    if not idx.exists:
        blockers.append(progress.Blocker(
            "小说缺 `00_进度.md`，无法判定任何前置的成熟度",
            progress.PROGRESS_FILE, None, "存在并登记各产出成熟度",
            "按骨架模板补建 `00_进度.md`"))
        return blockers

    if task == "正文":
        if not lay.outline.exists():
            blockers.append(progress.Blocker(
                "本章细纲不存在", L.rel(ctx.novel_dir, lay.outline), None, "定稿",
                "先跑 `build_prompt.py --task 细纲` 出细纲，冷读收敛后定稿"))
        elif not idx.is_at_least(lay.outline, "定稿"):
            blockers.append(progress.Blocker(
                "本章细纲未定稿——细纲缺陷会原样复制进之后每一版正文",
                L.rel(ctx.novel_dir, lay.outline), idx.status_of(lay.outline), "定稿",
                "跑 `review_manuscript.py --chapter-dir <本章> --mode outline` 收敛必改项，"
                "清零后在 `00_进度.md` 标定稿"))
        if not lay.opener_state.exists():
            blockers.append(progress.Blocker(
                "本章开篇状态未物化——正文的主要状态载荷",
                L.rel(ctx.novel_dir, lay.opener_state), None, "已物化",
                "跑 `build_state_snapshot.py --write-chapter-openers`"))
    else:  # 细纲
        if not lay.volume_plan.exists():
            blockers.append(progress.Blocker(
                "本卷大纲不存在", L.rel(ctx.novel_dir, lay.volume_plan), None, "定稿",
                "先产出本卷大纲（任务 10）"))
        elif not idx.is_at_least(lay.volume_plan, "定稿"):
            blockers.append(progress.Blocker(
                "本卷大纲未定稿——节拍表是本章事件归属的唯一权威",
                L.rel(ctx.novel_dir, lay.volume_plan), idx.status_of(lay.volume_plan), "定稿",
                "校验卷大纲（`audit_consistency.py --rule plan_beat` 归零）后标定稿"))
        prev = assemble.resolve_prev_manuscript(ctx)
        if lay.chapter > 1 and prev is None:
            blockers.append(progress.Blocker(
                "上一章正文未落位——滑动窗口取不到衔接素材",
                L.rel(ctx.novel_dir, lay.manuscript.parent / f"章{lay.chapter - 1:04d}.md"),
                None, "已落位（建议定稿）",
                "先把上一章正文落位到 `10_正文/…`"))
    return blockers


def main() -> int:
    ap = argparse.ArgumentParser(
        description="按六段骨架拼装自包含的云端提示词（正文 / 单章细纲）")
    ap.add_argument("--novel", help="小说目录，如 01_小说数据/00_苍玄")
    ap.add_argument("--chapter-dir", help="章工作区目录（可替代 --novel/--part/--volume/--chapter）")
    ap.add_argument("--task", choices=sorted(TASKS), required=True)
    ap.add_argument("--chapter", type=int, help="章号（如 3）")
    ap.add_argument("--part", type=int, default=1)
    ap.add_argument("--volume", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的提示词存档")
    ap.add_argument("--no-prebuild", action="store_true", help="不预建回填/目标空文件")
    args = ap.parse_args()

    if args.chapter_dir:
        cd = Path(args.chapter_dir).resolve()
        novel_dir = L.find_novel_dir(cd)
        part, volume, chapter = L.parse_chapter_dir(cd)
    else:
        if not args.novel or args.chapter is None:
            ap.error("需要 --novel 与 --chapter，或用 --chapter-dir")
        novel_dir = Path(args.novel).resolve()
        part, volume, chapter = args.part, args.volume, args.chapter

    try:
        lay = L.resolve(novel_dir, part, volume, chapter)
    except L.LayoutError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parents[2]
    ctx = assemble.Ctx(novel_dir=novel_dir, repo_root=repo_root, layout=lay,
                       novel_name=novel_dir.name.split("_", 1)[-1])

    # ── 前置门禁：不过就只出阻断报告，一个文件都不写 ──
    blockers = _gate(ctx, args.task)
    if blockers:
        print(progress.render_block_report(ctx.novel_name, TASKS[args.task][0], blockers))
        return 2

    prompt = (assemble.build_manuscript(ctx) if args.task == "正文"
              else assemble.build_outline(ctx))

    archive_name = TASKS[args.task][1]
    archive = lay.prompt_dir / archive_name
    target = lay.manuscript if args.task == "正文" else lay.outline
    text = prompt.render()

    # ── 报告 ──
    st = prompt.stats()
    print(f"《{ctx.novel_name}》{lay.chapter_id} · {TASKS[args.task][0]}")
    print(f"  提示词　　　{L.rel(novel_dir, archive)}")
    print(f"  体量　　　　{st['字节数'] // 1024} KB（{st['区块数']} 区块，"
          f"其中 {st['逐字内联区块']} 个逐字内联）")
    if ctx.missing:
        print(f"  源文件缺失　{len(ctx.missing)} 个：{'、'.join(ctx.missing[:6])}")
    if prompt.todos:
        print(f"  待人工确认　{len(prompt.todos)} 处（提示词内以 `>>>` 标出）：")
        for t in prompt.todos:
            print(f"    - {t}")

    leaks = prompt.leaks()
    print("  " + leak.render_report(leaks).replace("\n", "\n  "))

    # 细纲是逐字内联进【已有数据】的，同样会被模型转写成正文（规则第 6 条点名了
    # 【已有数据】）。工具不改写作者数据，只报出来——源文件里的标识由作者去清。
    src_leaks = prompt.source_leaks()
    if src_leaks:
        by_kind: dict[str, int] = {}
        for lk in src_leaks:
            by_kind[lk.kind] = by_kind.get(lk.kind, 0) + 1
        head = "、".join(f"{k} {v} 处" for k, v in sorted(by_kind.items()))
        print(f"  ⚠️ 内联细纲里有内部标识　{len(src_leaks)} 处（{head}）——"
              f"模型会照抄进正文，请回改细纲源文件：")
        for lk in src_leaks[:8]:
            print(f"    第 {lk.line_no} 行「{lk.hit}」→ {lk.fix}")
        if len(src_leaks) > 8:
            print(f"    …另有 {len(src_leaks) - 8} 处")

    if args.dry_run:
        print("\n（--dry-run：未写任何文件）")
        return 3 if leaks else 0

    if archive.exists() and not args.force:
        print(f"\n提示词存档已存在，未覆盖：{L.rel(novel_dir, archive)}\n"
              f"　　提示词是可重新生成的执行缓存——数据变了就该重拼，加 --force 覆盖。")
        return 3 if leaks else 0

    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(text, encoding="utf-8")
    print(f"\n已写出　{L.rel(novel_dir, archive)}")

    if not args.no_prebuild:
        created = L.prebuild(lay, archive_name, target)
        if created:
            print("已预建　" + "、".join(created))

    return 3 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
