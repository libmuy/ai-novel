#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
最小 LLM 客户端 (_llm.py)

仅用于 merge_chapter_state.py 的「描述」类字段智能合并（旧文本 + 新文本合并不丢信息）。
**只用 Python 标准库（urllib + tomllib），不引第三方 SDK。** 目标端点为 OpenAI 兼容的
`/chat/completions`。

配置
----
- `02_工具/llm.config.toml`（提交入库，无密钥）：
      base_url = "https://api.openai.com/v1"
      model = "gpt-4o-mini"
      api_key_env = "OPENAI_API_KEY"
      timeout = 60
      max_tokens = 4096
      temperature = 0.2
- `02_工具/llm.secret.toml`（可选，已 gitignore）：
      api_key = "sk-..."
- 密钥优先级：环境变量（api_key_env 指定名）> secret 文件 api_key > 无。
"""

import json
import os
import socket
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass

CONFIG_FILENAME = "llm.config.toml"
SECRET_FILENAME = "llm.secret.toml"


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


def load_llm_config(tools_dir):
    """从 tools_dir 加载 llm.config.toml（+ 可选 llm.secret.toml），解析出 LlmConfig。"""
    cfg_path = os.path.join(tools_dir, CONFIG_FILENAME)
    if not os.path.exists(cfg_path):
        raise LlmError(
            f"未找到 {cfg_path}。描述类字段的智能合并需要该配置文件；"
            f"可从 {CONFIG_FILENAME} 模板复制并填 base_url / model / api_key_env。"
        )
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
    )


def chat(cfg, system, user):
    """调用 OpenAI 兼容 /chat/completions，返回 assistant 文本内容。失败抛 LlmError。"""
    if not cfg.api_key:
        raise LlmError(
            f"缺少 API key：请设置环境变量 {cfg.api_key_env}，"
            f"或在 02_工具/{SECRET_FILENAME} 写 api_key。"
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

    req = urllib.request.Request(
        f"{cfg.base_url}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
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


def _strip_json_fence(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
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
