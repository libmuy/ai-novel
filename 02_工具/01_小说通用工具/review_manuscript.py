#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多模型冷读评审 (review_manuscript.py)

对云端产出的**正文**或**细纲**做「独立对抗性重读」——换一批模型、不看它自己的
思考过程，只挑逻辑硬伤。复现用户实验：别的模型冷读正文就能挑出前后矛盾/不闭合/
脑补/穿帮，缺的不是资料，是这一步。

用法
----
    review_manuscript.py --chapter-dir <章工作区目录> [--mode manuscript|outline]
    review_manuscript.py --manuscript <正文或细纲.md> [--novel-dir <小说目录>]

    --config <path>     默认 02_工具/00_系统级/review.config.toml
    --passes 1|2|both   覆盖 config；1=仅无参照 2=仅带参照
    --no-write          不写「校验记录」文件，只打 JSON 到 stdout
    --record <path>     覆盖「校验记录」写入路径

输出
----
- stdout: 合并发现 JSON（主 Agent 解析后分诊、分级、写修改提示词）
- 追加写「校验记录」（带时间戳分节，不覆盖已有人工内容）：
    正文 → <章>/02_状态/02_正文校验记录.md
    细纲 → <章>/02_状态/03_细纲对照记录.md

评审器池见 review.config.toml。两家外部评审器全不可用且 claude_subagent=false →
退出码 3、不写文件（不静默放行）。
"""
import argparse
import datetime as _dt
import json
import os
import re
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "00_系统级"))

DEFAULT_CONFIG = _HERE.parent / "00_系统级" / "review.config.toml"

# ---------------------------------------------------------------- 冷读清单

CHECKLIST_PASS1 = """第一遍·无任何参照——只读这段文字本身，找「章内自洽」问题：
1. 前后事实矛盾（同一人/物/地点，两处说法不兼容）
2. 数值/机制不闭合（钱、时间、数量、因果链算不通）
3. 物理/生理不合理（现实常识层面）
4. 引入未回收（有分量的道具/线索/威胁，本章给了戏份却没下文）
5. 人物反应与其已展现的性格/处境矛盾
6. 对话/独白透露的信息与其身份认知不符"""

CHECKLIST_PASS2 = """第二遍·带参照（细纲 + 世界基本法则 + 主角档案 + 禁用词表）——查穿帮：
1. 世界硬规则冲突（修炼/生死/因果/时空法则）
2. 现代科技/计量/材料词穿帮（对照禁用词表）
3. 与细纲的场景功能/钩子/伏笔埋设不符
4. 主角行为越过「主角人设红线」
5. 经济：资源等级与境界匹配、兑换比例、财富分级
6. 细纲外脑补：正文出现细纲未列的道具/机制/因果 → 一律标记（宁可少写不可脑补）"""

CHECKLIST_OUTLINE1 = """第一遍·无任何参照——只读这份细纲本身：
1. 场景功能是否重复（推进/揭示/情感/转折 四类，别两场同功能）
2. 是否连续两个「转折」场景（节奏禁忌）
3. 单场景是否畸重（同质连续 >800 字要切分）
4. 涉及数值/计量/经济的机制是否在细纲内闭合（单位、折算、克扣基数与结果）
5. 出场对象清单是否完整（@财务/@物品/@关系 别漏）
6. 前后自相矛盾之处"""

CHECKLIST_OUTLINE2 = """第二遍·带参照（卷纲 + 世界基本法则 + 主角档案 + 禁用词表）：
1. 与卷纲节拍表该章摘要的对象/钩子/伏笔是否一致
2. 主角对关键事件的反应是否对齐主角档案性格设定
3. 世界硬规则冲突
4. 引入的道具/线索是否登记了回收章或 FH 号
5. 现代词穿帮"""

_OUTPUT_SPEC = """只输出一个 JSON 对象，形如：
{"findings":[{"severity":"high|med|low","kind":"矛盾|不闭合|物理|未回收|人物|脑补|穿帮|世界规则|经济|节奏","where":"第几段或原文引文（20字内）","problem":"一句话说清问题","fix_hint":"改的方向，不强制"}]}
没有问题就输出 {"findings":[]}。不要输出 JSON 以外的任何文字。"""


# ---------------------------------------------------------------- 路径解析

def _resolve_targets(args):
    """→ (novel_dir: Path, mode: str, target_file: Path, ref: dict, record_path: Path|None)"""
    if args.chapter_dir:
        chdir = Path(args.chapter_dir).resolve()
        m_part = re.search(r"第0*(\d+)部", str(chdir))
        m_vol = re.search(r"卷0*(\d+)", str(chdir))
        m_ch = re.search(r"章0*(\d+)", chdir.name)
        if not (m_part and m_vol and m_ch):
            sys.exit(f"无法从 {chdir} 解析 部/卷/章 号")
        part, vol, ch = int(m_part.group(1)), int(m_vol.group(1)), int(m_ch.group(1))
        # 05_工作区/03_第NN部/03_卷NN/03_章XXXX → 向上 4 层是小说目录
        novel_dir = chdir.parents[3]
        vol_s, ch_s = f"{vol:02d}", f"{ch:04d}"

        def _glob1(base, pat):
            hits = sorted(novel_dir.glob(pat))
            return hits[0] if hits else base

        manuscript = _glob1(
            novel_dir / f"10_正文/01_第{part:02d}部/01_卷{vol_s}/章{ch_s}.md",
            f"10_正文/*第{part:02d}部*/*卷{vol_s}*/章{ch_s}.md")
        outline = _glob1(
            novel_dir / f"03_规划/01_第{part:02d}部/01_卷{vol_s}/规划_卷{vol_s}_章{ch_s}.md",
            f"03_规划/*第{part:02d}部*/*卷{vol_s}*/规划_卷{vol_s}_章{ch_s}.md")
        vol_outline = _glob1(
            novel_dir / f"03_规划/01_第{part:02d}部/01_卷{vol_s}/规划_卷{vol_s}.md",
            f"03_规划/*第{part:02d}部*/*卷{vol_s}*/规划_卷{vol_s}.md")

        mode = args.mode or "manuscript"
        if mode == "outline":
            target = outline
            record = chdir / "02_状态" / "03_细纲对照记录.md"
            ref = {"卷纲": vol_outline}
        else:
            target = manuscript
            record = chdir / "02_状态" / "02_正文校验记录.md"
            ref = {"细纲": outline}
    else:
        target = Path(args.manuscript).resolve()
        novel_dir = Path(args.novel_dir).resolve() if args.novel_dir else _find_novel_dir(target)
        mode = args.mode or ("outline" if "规划" in target.name or "细纲" in target.name else "manuscript")
        record = Path(args.record).resolve() if args.record else target.with_name(target.stem + "_冷读记录.md")
        ref = {}

    # 公共参照
    setto = novel_dir / "01_设定"
    ref["世界基本法则"] = setto / "00_小说概念.md"
    ref["主角档案"] = setto / "00_主角档案.md"
    if (setto / "00_主角档案_当前阶段.md").exists():
        ref["主角档案·当前阶段"] = setto / "00_主角档案_当前阶段.md"
    if (setto / "00_禁用词表.md").exists():
        ref["禁用词表"] = setto / "00_禁用词表.md"

    if args.record:
        record = Path(args.record).resolve()
    return novel_dir, mode, target, ref, record


def _find_novel_dir(p: Path) -> Path:
    for d in [p, *p.parents]:
        if (d / "01_设定").is_dir() and (d / "10_正文").is_dir():
            return d
    sys.exit(f"从 {p} 向上找不到小说目录（需含 01_设定/ 与 10_正文/）；用 --novel-dir 指定")


# ---------------------------------------------------------------- 评审器

def _load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _probe(host: str, port: int, timeout=3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _extract_json_obj(text: str) -> dict:
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if "{" in t and "}" in t:
        t = t[t.index("{"): t.rindex("}") + 1]
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _run_opencode(model: str, prompt: str, timeout: int) -> tuple[str, str]:
    """→ (raw_text, err)。免费档偶发服务器错 → 调用方重试/回退。"""
    try:
        proc = subprocess.run(
            ["opencode", "run", "--model", f"opencode/{model}", "--format", "json", prompt],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "", f"timeout({timeout}s)"
    except FileNotFoundError:
        return "", "opencode CLI 未安装"
    if proc.returncode != 0:
        return "", f"exit {proc.returncode}: {proc.stderr.strip()[:200]}"
    chunks = []
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "text":
            chunks.append(ev.get("part", {}).get("text", ""))
        elif ev.get("type") == "error":
            return "", f"model error: {json.dumps(ev.get('part', {}), ensure_ascii=False)[:200]}"
    return "".join(chunks).strip(), ""


def _critic_opencode(models, prompt, timeout):
    """按模型列表依次尝试，返回第一个成功的 (model, findings, err)。"""
    last_err = ""
    for model in models:
        for attempt in (1, 2):
            raw, err = _run_opencode(model, prompt, timeout)
            if err:
                last_err = f"{model}: {err}"
                continue
            obj = _extract_json_obj(raw)
            if "findings" in obj and isinstance(obj["findings"], list):
                return model, obj["findings"], ""
            last_err = f"{model}: 输出非预期 JSON（{raw[:120]}）"
        # 该模型两次都失败 → 下一个模型
    return None, [], last_err or "全部 opencode 模型不可用"


def _critic_qwen(prompt):
    try:
        import _llm
        cfg = _llm.load_llm_config()
        raw = _llm.chat(cfg, "你是严格的小说逻辑审校，只输出 JSON。", prompt)
        obj = _extract_json_obj(raw)
        if "findings" in obj and isinstance(obj["findings"], list):
            return obj["findings"], ""
        return [], f"输出非预期 JSON（{raw[:120]}）"
    except Exception as e:  # noqa: BLE001  — 评审器失败不应崩主流程
        return [], f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- 确定性检查

def _run_lexicon(novel_dir: Path, target: Path, mode: str):
    if mode != "manuscript":
        return []
    try:
        from audit.context import AuditContext
        from audit.rules.manuscript_lexicon import ManuscriptLexiconRule
        ctx = AuditContext(novel_dir)
        rel = target.resolve().relative_to(novel_dir).as_posix()
        out = []
        for f in ManuscriptLexiconRule().run(ctx):
            if f.code == "LEXICON001" and any(rel in loc for loc in f.locations):
                out.append({"code": f.code, "message": f.message,
                            "locations": [l for l in f.locations if rel in l]})
        return out
    except Exception as e:  # noqa: BLE001
        return [{"code": "LEXICON_ERR", "message": f"禁用词检查未跑成: {e}", "locations": []}]


# ---------------------------------------------------------------- 提示词与主流程

def _build_prompt(target_text, checklist, ref_texts: dict | None):
    parts = [f"下面是一部长篇小说的{'细纲' if ref_texts is None else '章节'}文本，请按清单冷读挑错。\n"]
    if ref_texts:
        for name, txt in ref_texts.items():
            parts.append(f"===== 参照·{name} =====\n{txt.strip()}\n")
    parts.append("===== 待审文本 =====\n" + target_text.strip() + "\n")
    parts.append("===== 冷读清单 =====\n" + checklist + "\n")
    parts.append("===== 输出要求 =====\n" + _OUTPUT_SPEC)
    return "\n".join(parts)


def _read(p) -> str:
    try:
        return Path(p).read_text(encoding="utf-8")
    except (OSError, TypeError):
        return ""


def main():
    ap = argparse.ArgumentParser(description="多模型冷读评审")
    ap.add_argument("--chapter-dir")
    ap.add_argument("--manuscript")
    ap.add_argument("--novel-dir")
    ap.add_argument("--mode", choices=["manuscript", "outline"])
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--passes", choices=["1", "2", "both"])
    ap.add_argument("--record")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()
    if not args.chapter_dir and not args.manuscript:
        ap.error("需要 --chapter-dir 或 --manuscript")

    cfg = _load_config(Path(args.config))
    crit = cfg.get("critics", {})
    runc = cfg.get("run", {})
    passes = args.passes or runc.get("passes", "both")

    novel_dir, mode, target, ref, record_path = _resolve_targets(args)
    if not target or not Path(target).exists():
        sys.exit(f"待审文本不存在: {target}")
    target_text = _read(target)

    # 参照文本
    ref_texts = {k: _read(v) for k, v in ref.items() if v and Path(v).exists()}

    if mode == "outline":
        cl1, cl2 = CHECKLIST_OUTLINE1, CHECKLIST_OUTLINE2
    else:
        cl1, cl2 = CHECKLIST_PASS1, CHECKLIST_PASS2

    jobs = []
    if passes in ("1", "both"):
        jobs.append(("无参照", _build_prompt(target_text, cl1, None)))
    if passes in ("2", "both"):
        jobs.append(("带参照", _build_prompt(target_text, cl2, ref_texts)))

    # ---- 评审器池 ----
    used, unavailable = [], []
    findings = []

    if crit.get("opencode", False):
        models = crit.get("opencode_models", [])
        for pass_name, prompt in jobs:
            model, fs, err = _critic_opencode(models, prompt, int(runc.get("opencode_timeout", 240)))
            if model:
                used.append(f"opencode/{model}·{pass_name}")
                for f in fs:
                    f["_source"] = f"opencode/{model}·{pass_name}"
                    findings.append(f)
            else:
                unavailable.append(f"opencode·{pass_name}: {err}")

    lq = crit.get("local_qwen", "auto")
    use_qwen = lq is True or (lq == "auto" and _probe("ai-station.local", 8080))
    if lq == "auto" and not use_qwen:
        unavailable.append("local_qwen: ai-station.local:8080 不可达（auto→跳过）")
    elif lq is True and not _probe("ai-station.local", 8080):
        unavailable.append("local_qwen: 配置强制 true 但端点不可达")
    if use_qwen:
        for pass_name, prompt in jobs:
            fs, err = _critic_qwen(prompt)
            if err:
                unavailable.append(f"local_qwen·{pass_name}: {err}")
            else:
                used.append(f"local_qwen·{pass_name}")
                for f in fs:
                    f["_source"] = f"local_qwen·{pass_name}"
                    findings.append(f)

    subagent_requested = bool(crit.get("claude_subagent", False))

    # ---- 确定性检查并入 ----
    lexicon = _run_lexicon(novel_dir, Path(target), mode)

    external_ok = len(used) > 0
    result = {
        "mode": mode,
        "target": str(Path(target).resolve().relative_to(novel_dir)),
        "passes": passes,
        "critics_used": used,
        "critics_unavailable": unavailable,
        "claude_subagent_requested": subagent_requested,
        "lexicon": lexicon,
        "findings": findings,
        "finding_count": len(findings),
    }

    if not external_ok and not subagent_requested:
        result["error"] = "无可用独立评审器（opencode + local_qwen 均不可用，claude_subagent=false）"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(3)

    if not args.no_write and record_path:
        _append_record(Path(record_path), result)
        result["record_written"] = str(record_path)

    print(json.dumps(result, ensure_ascii=False, indent=2))


def _append_record(path: Path, result: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"\n\n---\n\n## 冷读评审 · {ts}\n",
             f"> 脚本：`review_manuscript.py`（{result['mode']} / {result['passes']} 遍）",
             f"> 评审器：{', '.join(result['critics_used']) or '（无）'}"]
    if result["critics_unavailable"]:
        lines.append(f"> 不可用：{'; '.join(result['critics_unavailable'])}")
    if result.get("claude_subagent_requested"):
        lines.append("> `claude_subagent_requested=true` — 待主 Agent 另 spawn 冷读 subagent")
    lines.append("")
    if result["lexicon"]:
        lines.append("**确定性·禁用词命中**")
        for lx in result["lexicon"]:
            lines.append(f"- {lx['message']} — {', '.join(lx['locations'])}")
        lines.append("")
    if not result["findings"]:
        lines.append("_评审器未报发现（不代表无问题，人工仍须过一遍）。_")
    else:
        lines.append(f"**评审器发现（合并 {len(result['findings'])} 条，未分诊）**\n")
        for f in result["findings"]:
            sev = {"high": "🔴", "med": "🟡", "low": "⚪"}.get(str(f.get("severity", "")).lower(), "·")
            lines.append(f"- {sev} [{f.get('kind', '?')}] {f.get('where', '')} — "
                         f"{f.get('problem', '')}"
                         + (f"（改：{f['fix_hint']}）" if f.get("fix_hint") else "")
                         + f"  〈{f.get('_source', '')}〉")
    lines.append("\n> 下一步：主 Agent 分诊 + 分级 + 写外科手术式修改提示词（≤3 轮循环）。")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
