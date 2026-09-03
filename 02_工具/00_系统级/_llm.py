#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
最小 LLM 客户端 (_llm.py)

仅用于 merge_chapter_state.py 的「描述」类字段智能合并（旧文本 + 新文本合并不丢信息）。
**只用 Python 标准库（urllib + tomllib + subprocess），不引第三方 SDK。**

两种可插拔后端（体例同 tts_chapter.py 的 backend=edge|openai|piper|auto）：
- `http`     — OpenAI 兼容 `/chat/completions`（纯 urllib）。
- `opencode` — 子进程调用本机 `opencode` CLI（`opencode run --model <m> <prompt>`），
               免密钥、不依赖局域网端点，供 http 端点不可达时兜底。
- `auto`     — 先探测 base_url 的主机端口，可达就走 http；不可达则降级到 opencode
               （降级会在 stderr 打一行明确提示，不静默切换）；两者都不行才抛 LlmError。

配置
----
- `02_工具/00_系统级/llm.config.toml`（提交入库，无密钥）：
      backend = "auto"    # "http" | "opencode" | "auto"（默认，缺省同此）
      base_url = "http://ai-station.local:8080/v1"   # 或 https://api.openai.com/v1
      model = "..."
      api_key_required = false   # 本地无鉴权端点；默认 true
      api_key_env = "OPENAI_API_KEY"
      timeout = 120
      max_tokens = 4096
      temperature = 0.2
      # opencode 后端可选覆盖：
      # opencode_models = ["opencode/mimo-v2.5-free", "opencode/nemotron-3-ultra-free"]
      # opencode_timeout = 300
- `02_工具/00_系统级/llm.secret.toml`（可选，已 gitignore）：
      api_key = "sk-..."
- 密钥优先级：环境变量（api_key_env 指定名）> secret 文件 api_key > 无。
  api_key_required = false 时不发 Authorization 头、缺 key 也不报错（仅 http 后端相关）。
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

CONFIG_FILENAME = "llm.config.toml"
SECRET_FILENAME = "llm.secret.toml"

# opencode 后端默认试用的免费模型（按顺序试，前一个失败/空输出/超时则试下一个）
_OPENCODE_MODELS = ["opencode/mimo-v2.5-free", "opencode/nemotron-3-ultra-free"]


class LlmError(Exception):
    """LLM 配置缺失 / 网络失败 / 响应不合约。上层据此中止合并、不写文件。"""
    pass


@dataclass
class LlmConfig:
    base_url: str
    model: str
    api_key: str | None
    api_key_env: str
    timeout: int = 60
    max_tokens: int = 4096
    temperature: float = 0.2
    api_key_required: bool = True  # 本地无鉴权端点（llama.cpp 等）设 false
    backend: str = "auto"  # "http" | "opencode" | "auto"
    opencode_models: list = field(default_factory=lambda: list(_OPENCODE_MODELS))
    opencode_timeout: int = 300


def load_llm_config(tools_dir=None):
    """加载 llm.config.toml（+ 可选 llm.secret.toml），解析出 LlmConfig。
    先查 tools_dir，再退回本模块所在目录（02_工具/00_系统级/，配置与 _llm.py 同放）。"""
    own_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    if tools_dir:
        candidates.append(os.path.join(tools_dir, CONFIG_FILENAME))
    candidates.append(os.path.join(own_dir, CONFIG_FILENAME))
    cfg_path = next((p for p in candidates if os.path.exists(p)), None)
    if cfg_path is None:
        raise LlmError(
            f"未找到 {CONFIG_FILENAME}（查过 {', '.join(candidates)}）。"
            f"描述类字段的智能合并需要该配置文件；填 base_url / model / api_key_env。"
        )
    tools_dir = os.path.dirname(cfg_path)  # secret 文件与 config 同目录
    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)

    try:
        base_url = str(data["base_url"]).rstrip("/")
        model = str(data["model"])
        api_key_env = str(data["api_key_env"])
    except KeyError as e:
        raise LlmError(f"{cfg_path} 缺少必填项: {e}")

    api_key = os.environ.get(api_key_env) or None
    if not api_key:
        secret_path = os.path.join(tools_dir, SECRET_FILENAME)
        if os.path.exists(secret_path):
            with open(secret_path, "rb") as f:
                api_key = tomllib.load(f).get("api_key") or None

    return LlmConfig(
        base_url=base_url,
        model=model,
        api_key=api_key,
        api_key_env=api_key_env,
        timeout=int(data.get("timeout", 60)),
        max_tokens=int(data.get("max_tokens", 4096)),
        temperature=float(data.get("temperature", 0.2)),
        api_key_required=bool(data.get("api_key_required", True)),
        backend=str(data.get("backend", "auto")),
        opencode_models=list(data.get("opencode_models") or _OPENCODE_MODELS),
        opencode_timeout=int(data.get("opencode_timeout", 300)),
    )


def _host_port(base_url: str) -> str:
    u = urllib.parse.urlparse(base_url)
    port = u.port or (443 if u.scheme == "https" else 80)
    return f"{u.hostname}:{port}"


def _probe_base_url(base_url: str, timeout: float = 3.0) -> bool:
    u = urllib.parse.urlparse(base_url)
    if not u.hostname:
        return False
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        with socket.create_connection((u.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def _resolve_backend(cfg: LlmConfig) -> str:
    """backend != "auto" 原样返回；backend == "auto" 探测 base_url，不可达则降级 opencode。"""
    if cfg.backend != "auto":
        return cfg.backend
    if _probe_base_url(cfg.base_url, timeout=min(cfg.timeout, 3) or 3):
        return "http"
    if shutil.which("opencode") is None:
        raise LlmError(
            f"backend=auto：{_host_port(cfg.base_url)} 不可达，且本机未安装 opencode CLI"
            f"（which opencode 为空）。两条路径都走不通，无法调用 LLM。"
        )
    print(
        f"[_llm] {_host_port(cfg.base_url)} 不可达，降级到 {cfg.opencode_models[0]}",
        file=sys.stderr,
    )
    return "opencode"


def _opencode_chat(system: str, user: str, models=None, timeout: int = 300) -> str:
    """子进程调用本机 opencode CLI，按模型列表依次尝试，返回第一个成功的输出文本。"""
    models = models or _OPENCODE_MODELS
    prompt = system + "\n\n" + user
    last_err = None
    for model in models:
        try:
            r = subprocess.run(
                ["opencode", "run", "--model", model, prompt],
                capture_output=True, text=True, timeout=timeout,
            )
        except FileNotFoundError:
            raise LlmError("opencode CLI 未安装（which opencode 为空）")
        except subprocess.TimeoutExpired:
            last_err = f"{model} 超时({timeout}s)"
            continue
        if r.returncode != 0:
            last_err = f"{model} exit {r.returncode}: {(r.stderr or '').strip()[:300]}"
            continue
        out = (r.stdout or "").strip()
        if not out:
            last_err = f"{model} 空输出; stderr={(r.stderr or '')[:300]}"
            continue
        return out
    raise LlmError(f"opencode 全部失败: {last_err}")


def chat(cfg, system, user):
    """按 backend（http/opencode/auto）分派，返回 assistant 文本内容。失败抛 LlmError。"""
    backend = _resolve_backend(cfg)
    if backend == "opencode":
        return _opencode_chat(system, user, models=cfg.opencode_models, timeout=cfg.opencode_timeout)
    if backend != "http":
        raise LlmError(f"未知 backend: {backend!r}（合法值: http / opencode / auto）")
    return _http_chat(cfg, system, user)


def _http_chat(cfg, system, user):
    """调用 OpenAI 兼容 /chat/completions，返回 assistant 文本内容。失败抛 LlmError。"""
    if cfg.api_key_required and not cfg.api_key:
        raise LlmError(
            f"缺少 API key：请设置环境变量 {cfg.api_key_env}，"
            f"或在 02_工具/{SECRET_FILENAME} 写 api_key。"
            f"（本地无鉴权端点可在 {CONFIG_FILENAME} 设 api_key_required = false）"
        )

    body = json.dumps({
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"

    req = urllib.request.Request(
        f"{cfg.base_url}/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:500]
        except Exception:
            pass
        raise LlmError(f"LLM 接口返回 HTTP {e.code}: {detail}")
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        raise LlmError(f"LLM 接口请求失败: {e}")
    except json.JSONDecodeError as e:
        raise LlmError(f"LLM 响应不是合法 JSON: {e}")

    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LlmError(f"LLM 响应缺少 choices[0].message.content: {payload!r}")


_SYSTEM_PROMPT = (
    "你是长篇小说的状态维护助手。给你若干「状态字段」的旧描述与新描述，"
    "请把每个字段合并成一段自洽、完整、不丢信息、不重复、不矛盾的简洁中文描述。规则："
    "(1) 不得引入旧描述和新描述中都没有的新事实；"
    "(2) 冲突时以新描述为准，但保留旧描述里仍然成立的补充信息；"
    "(3) 只写该字段本身的描述，不加解释、标注或前后缀；"
    "(4) 保持简洁，一般不超过较长一方的 1.5 倍。"
)


import re as _re

_THINK_RE = _re.compile(r"<think>.*?</think>", _re.S)


def _strip_json_fence(text):
    t = text.strip()
    # 推理模型（Qwen3 等）可能前置 <think>...</think>
    t = _THINK_RE.sub("", t).strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    t = t.strip()
    # 兜底：若仍有包裹文字，截取第一个 { 到最后一个 }
    if not t.startswith("{") and "{" in t and "}" in t:
        t = t[t.index("{"): t.rindex("}") + 1]
    return t.strip()


def merge_descriptive_fields(cfg, items):
    """
    一次 API 调用合并全部描述字段。
    items: [(object_id, field, old_text, new_text), ...]
    返回:  {(object_id, field): merged_text}
    """
    if not items:
        return {}

    spec = {
        str(i + 1): {
            "对象": obj_id, "字段": field, "旧值": old, "新值": new,
        }
        for i, (obj_id, field, old, new) in enumerate(items)
    }
    user = (
        "请合并下列字段。**仅**输出一个 JSON 对象，key 为下面每项的序号字符串，"
        "value 为合并后的描述文本，不要输出任何多余内容。\n"
        "```json\n" + json.dumps(spec, ensure_ascii=False, indent=1) + "\n```"
    )

    content = _strip_json_fence(chat(cfg, _SYSTEM_PROMPT, user))
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise LlmError(f"LLM 返回的合并结果不是合法 JSON: {e}\n原文: {content[:500]}")
    if not isinstance(parsed, dict):
        raise LlmError(f"LLM 返回的合并结果不是 JSON 对象: {content[:500]}")

    result = {}
    for i, (obj_id, field, _old, _new) in enumerate(items):
        k = str(i + 1)
        if k not in parsed or not isinstance(parsed[k], str) or not parsed[k].strip():
            raise LlmError(f"LLM 返回缺少字段 {k}（{obj_id}.{field}）或值为空: {content[:500]}")
        result[(obj_id, field)] = parsed[k].strip()
    return result
