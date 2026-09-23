#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地模型执行云端提示词 (run_prompt.py)

把 `build_prompt.py` 拼好的自包含提示词存档（去掉头部「本地用」说明块后）喂给本地 llama.cpp 端点，
产出按 WS006 同名配对落到 `01_模型输出/`。只做「读提示词 → 调本地模型 → 写产出」，
不落位 canonical path（落位是验收步骤，规格见技能 `04_单章质量验收.md`）。

用法
----
    run_prompt.py --chapter-dir <章工作区目录> --task 正文|细纲 [--revision N]
    run_prompt.py --prompt <提示词.md> --out <产出.md>          # 任意路径（校准/对照用）

    --max-tokens N     缺省 8192（llm.config.toml 的 4096 对正文不够）
    --timeout S        缺省 900（单次生成，含预填）
    --temperature T    缺省沿用 llm.config.toml
    --think|--no-think 思考模式；缺省沿用 llm.config.toml 的 enable_thinking
    --save-raw         另存原始输出（含服务端单独返回的 reasoning_content）到 <产出>.raw.txt，诊断思考是否循环
    --force            覆盖已存在的产出
    --dry-run          只报 token 数与预计耗时，不调生成、不写文件

与 `_llm.chat()` 的差异（有意为之）
----------------------------------
- 强制 http 后端：`backend=auto` 在端点不可达时会静默降级到 opencode 免费模型，
  那就不再是「纯本地」实验了，这里宁可报错。
- 自己发请求：`chat()` 只返回文本，拿不到 llama.cpp 的 `timings` 与 `finish_reason`
  （后者用来发现正文被 max_tokens 截断）。
- 所有参数按调用覆盖，**不改 llm.config.toml**（merge_chapter_state.py 共用它）。

退出码
------
    0 = 已写出    1 = 用法/读取错误    2 = 端点不可达或调用失败
    3 = 已写出但被 max_tokens 截断（finish_reason=length）——产出不完整，勿当定稿
"""
import argparse
import dataclasses
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "00_系统级"))

import _llm  # noqa: E402

TASK_FILES = {"正文": "01_正文生成", "细纲": "00_单章细纲"}
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TIMEOUT = 900
_SYSTEM = "你是长篇小说的创作助手。提示词已完整给出角色、规则、数据与任务，请严格按其执行。"


def _resolve_paths(args) -> tuple[Path, Path]:
    if args.prompt:
        prompt = Path(args.prompt)
        if not args.out:
            raise SystemExit("--prompt 模式必须同时给 --out")
        return prompt, Path(args.out)
    if not (args.chapter_dir and args.task):
        raise SystemExit("需要 --chapter-dir 与 --task，或 --prompt 与 --out")
    stem = TASK_FILES[args.task] + (f"_修订{args.revision}" if args.revision else "")
    chapter = Path(args.chapter_dir)
    return chapter / "00_提示词" / f"{stem}.md", chapter / "01_模型输出" / f"{stem}.md"


def _strip_archive_preamble(text: str) -> str:
    """提示词存档开头有一段「本地用，不必发给云端」的 `>` 引用块（含仓库路径与脚本名），
    正文从第一个 `# 【…】` 一级标题起。云端流程本就不发这段，这里同样剥掉。"""
    m = re.search(r"^# 【", text, re.M)
    if m and m.start() > 0 and text.lstrip().startswith(">"):
        return text[m.start():]
    return text


def _server_root(base_url: str) -> str:
    return base_url[:-3] if base_url.endswith("/v1") else base_url


def _count_tokens(cfg, text: str) -> int | None:
    req = urllib.request.Request(
        f"{_server_root(cfg.base_url)}/tokenize",
        data=json.dumps({"content": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return len(json.load(resp)["tokens"])
    except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError):
        return None


def _generate(cfg, prompt: str) -> dict:
    body = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": prompt}],
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    if cfg.enable_thinking is not None:
        body["chat_template_kwargs"] = {"enable_thinking": cfg.enable_thinking}
    req = urllib.request.Request(
        f"{cfg.base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise _llm.LlmError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
    except (urllib.error.URLError, OSError) as e:
        raise _llm.LlmError(f"请求失败: {e}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="用本地模型执行云端提示词存档")
    ap.add_argument("--chapter-dir")
    ap.add_argument("--task", choices=list(TASK_FILES))
    ap.add_argument("--revision", type=int)
    ap.add_argument("--prompt")
    ap.add_argument("--out")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--temperature", type=float)
    think = ap.add_mutually_exclusive_group()
    think.add_argument("--think", dest="think", action="store_true", default=None)
    think.add_argument("--no-think", dest="think", action="store_false")
    ap.add_argument("--save-raw", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    prompt_path, out_path = _resolve_paths(args)
    if not prompt_path.is_file():
        print(f"提示词不存在: {prompt_path}", file=sys.stderr)
        return 1
    if out_path.exists() and not (args.force or args.dry_run):
        print(f"产出已存在，拒绝覆盖（--force 可强制）: {out_path}", file=sys.stderr)
        return 1
    raw_prompt = prompt_path.read_text(encoding="utf-8")
    prompt = _strip_archive_preamble(raw_prompt)

    try:
        cfg = _llm.load_llm_config()
    except _llm.LlmError as e:
        print(e, file=sys.stderr)
        return 1
    overrides = {"backend": "http", "max_tokens": args.max_tokens, "timeout": args.timeout}
    if args.temperature is not None:
        overrides["temperature"] = args.temperature
    if args.think is not None:
        overrides["enable_thinking"] = args.think
    cfg = dataclasses.replace(cfg, **overrides)

    if not _llm._probe_base_url(cfg.base_url):
        print(f"本地端点不可达: {_llm._host_port(cfg.base_url)}（已强制 http，不降级 opencode）",
              file=sys.stderr)
        return 2

    n_prompt = _count_tokens(cfg, prompt)
    print(f"提示词  {prompt_path}  {len(prompt)} 字符 / "
          f"{n_prompt if n_prompt is not None else '?'} tokens", file=sys.stderr)
    if len(prompt) != len(raw_prompt):
        print(f"剥离    存档头部说明 {len(raw_prompt) - len(prompt)} 字符（本地用，不发给模型）", file=sys.stderr)
    print(f"参数    max_tokens={cfg.max_tokens} temperature={cfg.temperature} "
          f"thinking={cfg.enable_thinking} timeout={cfg.timeout}s", file=sys.stderr)
    if args.dry_run:
        if n_prompt:
            print(f"预计    预填约 {n_prompt / 550:.0f}s（按 550 tok/s，实测值；"
                  f"生成另计，~30 tok/s）", file=sys.stderr)
        print("（--dry-run：未生成、未写文件）", file=sys.stderr)
        return 0

    t0 = time.time()
    try:
        resp = _generate(cfg, prompt)
    except _llm.LlmError as e:
        print(f"生成失败: {e}", file=sys.stderr)
        return 2
    wall = time.time() - t0

    try:
        choice = resp["choices"][0]
        text = choice["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        print(f"响应缺少 choices[0].message.content: {str(resp)[:500]}", file=sys.stderr)
        return 2
    finish = choice.get("finish_reason")
    if args.save_raw:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        reasoning = choice["message"].get("reasoning_content") or ""
        raw = (f"<reasoning_content>\n{reasoning}\n</reasoning_content>\n\n" if reasoning else "") + text
        out_path.with_name(out_path.name + ".raw.txt").write_text(raw, encoding="utf-8")
    text = _llm._THINK_RE.sub("", text).strip()
    if "<think>" in text:  # 思考未闭合：被截断在思考里，没有正文
        text = text.split("<think>", 1)[0].strip()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text + "\n", encoding="utf-8")

    usage, tm = resp.get("usage") or {}, resp.get("timings") or {}
    print(f"产出    {out_path}  {len(text)} 字符", file=sys.stderr)
    print(f"用量    prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')} "
          f"finish={finish} wall={wall:.0f}s", file=sys.stderr)
    if tm:
        print(f"速度    预填 {tm.get('prompt_per_second', 0):.0f} tok/s，"
              f"生成 {tm.get('predicted_per_second', 0):.1f} tok/s", file=sys.stderr)
    if finish == "length":
        print("警告    finish_reason=length：被 max_tokens 截断，产出不完整", file=sys.stderr)
        return 3
    if not text:
        why = ("思考耗尽了 max_tokens" if finish == "length"
               else "模型在思考里就结束了、没给出最终答案（finish=stop，属思考模式的失败模式，换 --no-think 或重试）")
        print(f"警告    产出为空：{why}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
