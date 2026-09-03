#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节配音 (tts_chapter.py)

把一章**已定稿的正文**用 TTS 读成音频存档，供作者「听稿校对」。单一旁白音色，
正文预处理只剥离场景分隔符 `※` 等非正文标记。可插拔后端：edge（微软在线，默认）/
openai（OpenAI 兼容 /v1/audio/speech）/ piper（本机离线）/ auto。

用法
----
    tts_chapter.py --chapter-dir <章工作区目录> [选项]
    tts_chapter.py --manuscript <正文.md> --out <输出.mp3> [--novel-dir DIR] [选项]

      --backend edge|openai|piper|auto   覆盖 tts.config.toml
      --voice NAME        覆盖音色
      --rate ±N%          覆盖语速（如 -10% / +15%）
      --per-scene         每场景一个音频文件，不拼接
      --force             源文本/参数未变也强制重新合成
      --dry-run           只打印计划（场景数/字数/输出路径/后端），不写盘
      --config PATH       默认 02_工具/00_系统级/tts.config.toml
      --format json|text  默认 json（stdout）

输出
----
- 音频：`<章工作区>/03_音频/章{C:04d}.mp3`（--per-scene 时为 `章{C:04d}_场{N}.mp3`）
- manifest：`<章工作区>/03_音频/章{C:04d}.json`（源哈希 + 后端 + 音色；用于幂等跳过）
- stdout：结果 JSON（或 --format text 一行）

退出码：0 正常 / 1 路径或参数错 / 2 后端不可用（不写文件、不静默放行）
"""
import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

_HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = _HERE.parent / "00_系统级" / "tts.config.toml"
VENV_EDGE_TTS = _HERE.parent / ".venv" / "bin" / "edge-tts"
SETUP_HINT = "bash 02_工具/01_小说通用工具/tts/setup.sh"
TOOL_VERSION = "1.0"

SCENE_MARKERS = {"※", "＊", "***", "* * *", "＊＊＊", "* * * *"}
_HEADING_RE = re.compile(r"^#{1,6}\s")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


class TtsError(Exception):
    """路径/参数错。退出码 1。"""


class TtsBackendError(Exception):
    """后端不可用或全部合成失败。退出码 2，不写文件。"""


# ---------------------------------------------------------------- 原子写

def _atomic_write_bytes(path: Path, data: bytes):
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str):
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 正文预处理

def _clean_text(raw: str) -> list[str]:
    """正文 → 场景文本列表。剥离 BOM / frontmatter / HTML 注释 / 标题行 / 场景分隔符。"""
    raw = raw.lstrip("﻿")
    if raw.startswith("---\n") or raw.startswith("---\r\n"):
        parts = re.split(r"\r?\n---\r?\n", raw, maxsplit=1)
        if len(parts) == 2:
            raw = parts[1]
    raw = _COMMENT_RE.sub("", raw)

    scenes: list[list[str]] = [[]]
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped in SCENE_MARKERS:
            scenes.append([])
            continue
        if _HEADING_RE.match(stripped):
            continue
        scenes[-1].append(line)

    out: list[str] = []
    for buf in scenes:
        text = "\n".join(buf)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if text:
            out.append(text)
    return out


def _paragraph_chunks(scene: str, limit: int) -> list[str]:
    """单场景过长时按段落切，尽量不超过 limit 字。"""
    if len(scene) <= limit:
        return [scene]
    chunks, cur = [], ""
    for para in scene.split("\n\n"):
        if cur and len(cur) + len(para) + 2 > limit:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    return chunks


# ---------------------------------------------------------------- 路径解析

def _find_novel_dir(p: Path) -> Path:
    for d in [p, *p.parents]:
        if (d / "01_设定").is_dir() and (d / "10_正文").is_dir():
            return d
    raise TtsError(f"从 {p} 向上找不到小说目录（需含 01_设定/ 与 10_正文/）；用 --novel-dir 指定")


def _resolve_targets(args):
    """→ (novel_dir, manuscript: Path, out_dir: Path, stem: str, ext: str)"""
    ext = "mp3"
    if args.chapter_dir:
        chdir = Path(args.chapter_dir).resolve()
        m_part = re.search(r"第0*(\d+)部", str(chdir))
        m_vol = re.search(r"卷0*(\d+)", str(chdir))
        m_ch = re.search(r"章0*(\d+)", chdir.name)
        if not (m_part and m_vol and m_ch):
            raise TtsError(f"无法从 {chdir} 解析 部/卷/章 号")
        part, vol, ch = int(m_part.group(1)), int(m_vol.group(1)), int(m_ch.group(1))
        if len(chdir.parents) < 4:
            raise TtsError(f"{chdir} 层级不足，不像章工作区目录")
        novel_dir = chdir.parents[3]
        hits = sorted(novel_dir.glob(f"10_正文/*第{part:02d}部*/*卷{vol:02d}*/章{ch:04d}.md"))
        manuscript = hits[0] if hits else (
            novel_dir / f"10_正文/01_第{part:02d}部/01_卷{vol:02d}/章{ch:04d}.md")
        out_dir = chdir / "03_音频"
        stem = f"章{ch:04d}"
    else:
        manuscript = Path(args.manuscript).resolve()
        novel_dir = Path(args.novel_dir).resolve() if args.novel_dir else _find_novel_dir(manuscript)
        if not args.out:
            raise TtsError("--manuscript 模式需要 --out 指定输出音频路径")
        out_path = Path(args.out).resolve()
        out_dir = out_path.parent
        stem = out_path.stem
        if out_path.suffix:
            ext = out_path.suffix.lstrip(".")
    return novel_dir, manuscript, out_dir, stem, ext


# ---------------------------------------------------------------- 后端

def _probe_host(url: str, timeout: float = 3.0) -> bool:
    try:
        u = urlparse(url)
        port = u.port or (443 if u.scheme == "https" else 80)
        with socket.create_connection((u.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def _edge_bin() -> str | None:
    if VENV_EDGE_TTS.exists() and os.access(VENV_EDGE_TTS, os.X_OK):
        return str(VENV_EDGE_TTS)
    return shutil.which("edge-tts")


def _resolve_backend(name: str, cfg: dict) -> str:
    if name != "auto":
        return name
    if _probe_host(cfg.get("openai", {}).get("base_url", "")):
        return "openai"
    if _edge_bin():
        return "edge"
    raise TtsBackendError(
        "backend=auto：openai 端点不可达，且未找到 edge-tts 可执行文件。"
        f"装 edge-tts：{SETUP_HINT}"
    )


def _synth_edge(cfg: dict, opts: dict, text: str, out_path: Path):
    binary = _edge_bin()
    if not binary:
        raise TtsBackendError(f"未找到 edge-tts 可执行文件（查过 {VENV_EDGE_TTS} 与 PATH）。装它：{SETUP_HINT}")
    e = cfg.get("edge", {})
    voice = opts.get("voice") or e.get("voice", "zh-CN-YunxiNeural")
    rate = opts.get("rate") or e.get("rate", "+0%")
    volume = e.get("volume", "+0%")
    pitch = e.get("pitch", "+0Hz")
    timeout = int(cfg.get("run", {}).get("edge_timeout", 180))
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as tf:
        tf.write(text)
        txt_path = tf.name
    try:
        proc = subprocess.run(
            [binary, "--voice", voice, f"--rate={rate}", f"--volume={volume}",
             f"--pitch={pitch}", "--file", txt_path, "--write-media", str(out_path)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise TtsBackendError(f"edge-tts 超时（{timeout}s）")
    finally:
        os.unlink(txt_path)
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        raise TtsBackendError(f"edge-tts 失败 (exit {proc.returncode}): {proc.stderr.strip()[:300]}")


def _synth_openai(cfg: dict, opts: dict, text: str, out_path: Path):
    o = cfg.get("openai", {})
    base_url = str(o.get("base_url", "")).rstrip("/")
    if not base_url:
        raise TtsBackendError("tts.config.toml [openai].base_url 未配置")
    api_key = os.environ.get(o.get("api_key_env", "OPENAI_API_KEY")) or _secret_api_key()
    if o.get("api_key_required", False) and not api_key:
        raise TtsBackendError("openai 端点需要 api_key：设环境变量或写 02_工具/00_系统级/tts.secret.toml")
    body = json.dumps({
        "model": o.get("model", "tts-1"),
        "input": text,
        "voice": opts.get("voice") or o.get("voice", "alloy"),
        "response_format": o.get("response_format", "mp3"),
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(f"{base_url}/audio/speech", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=int(o.get("timeout", 300))) as resp:
            data = resp.read()
    except urllib.error.HTTPError as ex:
        raise TtsBackendError(f"openai TTS HTTP {ex.code}: {ex.read()[:300]!r}")
    except (urllib.error.URLError, socket.timeout, TimeoutError) as ex:
        raise TtsBackendError(f"openai TTS 请求失败: {ex}")
    if not data:
        raise TtsBackendError("openai TTS 返回空响应体")
    _atomic_write_bytes(out_path, data)


def _synth_piper(cfg: dict, opts: dict, text: str, out_path: Path):
    model = cfg.get("piper", {}).get("model", "")
    binary = shutil.which("piper")
    if not model or not binary:
        raise TtsBackendError("piper 后端未配置（需 tts.config.toml [piper].model + PATH 里的 piper 二进制）")
    try:
        proc = subprocess.run(
            [binary, "--model", model, "--output_file", str(out_path)],
            input=text, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        raise TtsBackendError("piper 超时（600s）")
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        raise TtsBackendError(f"piper 失败 (exit {proc.returncode}): {proc.stderr.strip()[:300]}")


_BACKENDS = {"edge": _synth_edge, "openai": _synth_openai, "piper": _synth_piper}


def _secret_api_key() -> str | None:
    secret = DEFAULT_CONFIG.with_name("tts.secret.toml")
    if secret.exists():
        with open(secret, "rb") as f:
            return tomllib.load(f).get("api_key") or None
    return None


def _synthesize_scene(backend: str, cfg: dict, opts: dict, text: str, out_path: Path):
    """单段合成（含重试）。集中的外部调用点，测试在此打桩。"""
    fn = _BACKENDS.get(backend)
    if fn is None:
        raise TtsBackendError(f"未知后端: {backend}")
    retry = int(cfg.get("run", {}).get("retry", 1))
    last = None
    for attempt in range(retry + 1):
        try:
            fn(cfg, opts, text, out_path)
            return
        except TtsBackendError as ex:
            last = ex
    raise last


def _concat_files(parts: list[Path], dest: Path):
    """裸字节拼接多段音频（edge/openai 的 CBR MPEG 帧拼接，听稿够用）。"""
    buf = bytearray()
    for p in parts:
        buf += p.read_bytes()
    _atomic_write_bytes(dest, bytes(buf))


# ---------------------------------------------------------------- manifest

def _read_manifest(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _manifest_key(source_sha: str, backend: str, opts: dict, per_scene: bool) -> dict:
    return {
        "source_sha256": source_sha,
        "backend": backend,
        "voice": opts.get("voice") or "",
        "rate": opts.get("rate") or "",
        "per_scene": per_scene,
    }


# ---------------------------------------------------------------- 主流程

def _load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def run(args) -> dict:
    cfg = _load_config(Path(args.config))
    out_cfg = cfg.get("output", {})
    per_scene = args.per_scene or bool(out_cfg.get("per_scene", False))

    novel_dir, manuscript, out_dir, stem, ext = _resolve_targets(args)
    if not manuscript.exists():
        raise TtsError(f"正文不存在: {manuscript}")

    raw = manuscript.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    scenes = _clean_text(raw.decode("utf-8"))
    if not scenes:
        raise TtsError(f"正文清洗后为空: {manuscript}")
    char_count = sum(len(re.sub(r"\s", "", s)) for s in scenes)

    backend = _resolve_backend(args.backend or cfg.get("backend", "edge"), cfg)
    if backend == "piper":
        ext = "wav"

    bcfg = cfg.get(backend, {})
    opts = {
        "voice": args.voice or bcfg.get("voice", ""),
        "rate": args.rate or bcfg.get("rate", ""),
    }

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(novel_dir))
        except ValueError:
            return str(p)

    audio_names = ([f"{stem}_场{i + 1}.{ext}" for i in range(len(scenes))]
                   if per_scene else [f"{stem}.{ext}"])
    manifest_path = out_dir / f"{stem}.json"
    result = {
        "chapter": stem,
        "manuscript": _rel(manuscript),
        "backend": backend,
        "voice": opts.get("voice") or cfg.get(backend, {}).get("voice", ""),
        "scene_count": len(scenes),
        "char_count": char_count,
        "per_scene": per_scene,
        "outputs": [_rel(out_dir / n) for n in audio_names],
        "manifest": _rel(manifest_path),
        "dry_run": bool(args.dry_run),
        "skipped": False,
    }

    if args.dry_run:
        return result

    key = _manifest_key(source_sha, backend, opts, per_scene)
    prev = _read_manifest(manifest_path)
    outputs_exist = all((out_dir / n).exists() for n in audio_names)
    if not args.force and outputs_exist and {k: prev.get(k) for k in key} == key:
        result["skipped"] = True
        return result

    out_dir.mkdir(parents=True, exist_ok=True)
    limit = int(out_cfg.get("chunk_char_limit", 4000))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        scene_files: list[Path] = []
        for si, scene in enumerate(scenes):
            chunks = _paragraph_chunks(scene, limit)
            chunk_files = []
            for ci, chunk in enumerate(chunks):
                cf = tmp / f"s{si}_c{ci}.{ext}"
                _synthesize_scene(backend, cfg, opts, chunk, cf)
                chunk_files.append(cf)
            sf = tmp / f"scene_{si}.{ext}"
            if len(chunk_files) == 1:
                shutil.copyfile(chunk_files[0], sf)
            else:
                _concat_files(chunk_files, sf)
            scene_files.append(sf)

        if per_scene:
            for name, sf in zip(audio_names, scene_files):
                _atomic_write_bytes(out_dir / name, sf.read_bytes())
        else:
            if len(scene_files) == 1:
                _atomic_write_bytes(out_dir / audio_names[0], scene_files[0].read_bytes())
            else:
                _concat_files(scene_files, out_dir / audio_names[0])

    total_bytes = sum((out_dir / n).stat().st_size for n in audio_names)
    result["bytes"] = total_bytes
    manifest = dict(key)
    manifest.update({
        "chapter": stem,
        "manuscript": _rel(manuscript),
        "scene_count": len(scenes),
        "char_count": char_count,
        "outputs": audio_names,
        "bytes": total_bytes,
        "tool_version": TOOL_VERSION,
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
    })
    _atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return result


def _print_text(r: dict):
    if r.get("skipped"):
        print(f"skipped: unchanged — {r['manifest']}")
        return
    tag = "[dry-run] " if r.get("dry_run") else ""
    size = f", {r['bytes'] / 1024:.0f} KiB" if r.get("bytes") else ""
    print(f"{tag}{r['chapter']}: {r['scene_count']} 场景 / {r['char_count']} 字 "
          f"→ {r['backend']}({r['voice']}) → {', '.join(r['outputs'])}{size}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="章节配音（TTS）")
    ap.add_argument("--chapter-dir")
    ap.add_argument("--manuscript")
    ap.add_argument("--novel-dir")
    ap.add_argument("--out")
    ap.add_argument("--backend", choices=["edge", "openai", "piper", "auto"])
    ap.add_argument("--voice")
    ap.add_argument("--rate")
    ap.add_argument("--per-scene", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--format", choices=["json", "text"], default="json")
    args = ap.parse_args(argv)
    if not args.chapter_dir and not args.manuscript:
        ap.error("需要 --chapter-dir 或 --manuscript")

    try:
        result = run(args)
    except TtsError as ex:
        print(json.dumps({"error": str(ex)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    except TtsBackendError as ex:
        print(json.dumps({"error": str(ex), "backend_unavailable": True}, ensure_ascii=False))
        sys.exit(2)

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_text(result)


if __name__ == "__main__":
    main()
