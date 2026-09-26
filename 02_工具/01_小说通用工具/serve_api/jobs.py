# -*- coding: utf-8 -*-
"""`/api/jobs` 后台任务：子进程编排、去重、临时 review 配置。"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from prompt_build import layout as L  # noqa: E402

from . import paths

_VOICES = [
    {"id": "", "label": "配置默认（见 tts.config.toml）"},
    {"id": "zh-CN-YunxiNeural", "label": "男声 · 云希"},
    {"id": "zh-CN-XiaoxiaoNeural", "label": "女声 · 晓晓"},
]
# 大纲/校验类任务本轮只占位接口，runner 下一轮再补（见计划 §指令 → 任务）
_UNIMPLEMENTED_JOBS = {
    "outline_book": "全书大纲提示词",
    "outline_part": "部大纲提示词",
    "outline_vol": "卷大纲提示词",
    "check": "一致性校验提示词",
}


@dataclass
class Job:
    id: str
    kind: str
    cmd: list
    cwd: str
    tmp_files: list = field(default_factory=list)
    status: str = "running"
    returncode: int | None = None
    log: str = ""
    started_at: float = field(default_factory=time.time)


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()
_RUNNING_KEYS: set[str] = set()
_RUNNING_LOCK = threading.Lock()


def _toml_val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_val(x) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)


def _toml_dump(d: dict) -> str:
    lines = []
    for name, table in d.items():
        if not isinstance(table, dict):
            continue
        lines.append(f"[{name}]")
        for k, v in table.items():
            lines.append(f"{k} = {_toml_val(v)}")
        lines.append("")
    return "\n".join(lines)


def _write_temp_review_config(engine: str, body: dict) -> Path:
    """按引擎选择临时覆盖 review.config.toml 的 [critics] 段，其余（超时/遍数）沿用原配置。"""
    base = tomllib.loads((paths.SYS_DIR / "review.config.toml").read_text(encoding="utf-8"))
    critics = dict(base.get("critics", {}))
    if engine == "opencode":
        critics["opencode"] = True
        critics["local_qwen"] = False
        models = body.get("oc_models")
        if models:
            critics["opencode_models"] = list(models)
    else:
        critics["opencode"] = False
        critics["local_qwen"] = True
    base["critics"] = critics
    fd, path = tempfile.mkstemp(suffix=".toml", prefix="review_ui_", dir=tempfile.gettempdir())
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(_toml_dump(base))
    return Path(path)


def _cmd_outline_ch(novel_dir, part, vol, ch, body):
    lay = L.resolve(novel_dir, part, vol, ch)
    cmd = [sys.executable, str(paths.TOOLS_DIR / "build_prompt.py"), "--chapter-dir", str(lay.chapter_dir),
           "--task", "细纲", "--no-prebuild-target"]
    if body.get("force"):
        cmd.append("--force")
    return cmd, []


def _cmd_draft(novel_dir, part, vol, ch, body):
    lay = L.resolve(novel_dir, part, vol, ch)
    cmd = [sys.executable, str(paths.TOOLS_DIR / "build_prompt.py"), "--chapter-dir", str(lay.chapter_dir),
           "--task", "正文", "--no-prebuild-target"]
    if body.get("force"):
        cmd.append("--force")
    return cmd, []


def _cmd_cold(novel_dir, part, vol, ch, body):
    lay = L.resolve(novel_dir, part, vol, ch)
    mode = "outline" if body.get("mode") == "outline" else "manuscript"
    cfg_path = _write_temp_review_config(body.get("engine", "local"), body)
    cmd = [sys.executable, str(paths.TOOLS_DIR / "review_manuscript.py"), "--chapter-dir", str(lay.chapter_dir),
           "--mode", mode, "--config", str(cfg_path)]
    return cmd, [cfg_path]


def _cmd_audio(novel_dir, part, vol, ch, body):
    lay = L.resolve(novel_dir, part, vol, ch)
    cmd = [sys.executable, str(paths.TOOLS_DIR / "tts_chapter.py"), "--chapter-dir", str(lay.chapter_dir),
           "--format", "json", "--force"]
    if body.get("per_scene"):
        cmd.append("--per-scene")
    voice = body.get("voice")
    if voice:
        cmd += ["--voice", voice]
    return cmd, []


_JOB_BUILDERS = {"outline_ch": _cmd_outline_ch, "draft": _cmd_draft, "cold": _cmd_cold, "audio": _cmd_audio}


def _run_job(job: Job):
    try:
        p = subprocess.run(job.cmd, cwd=job.cwd, capture_output=True, text=True, timeout=1800)
        job.log = ((p.stdout or "") + (("\n" + p.stderr) if p.stderr else ""))[-20000:]
        job.returncode = p.returncode
        # build_prompt.py: 0=已生成 2=前置门禁未过 3=生成了但内部标识自检未过（已写文件，需人工复核）
        job.status = "ok" if p.returncode == 0 else ("warn" if p.returncode == 3 else "fail")
    except subprocess.TimeoutExpired as e:
        job.log = f"超时（1800s）：{e}"
        job.status = "fail"
    except Exception as e:  # noqa: BLE001
        job.log = str(e)
        job.status = "fail"
    finally:
        for f in job.tmp_files:
            try:
                Path(f).unlink(missing_ok=True)
            except OSError:
                pass
        with _RUNNING_LOCK:
            _RUNNING_KEYS.discard(f"{job.kind}:{job.id_key}")


def _create_job(novel_dir: Path, body: dict) -> tuple[int, dict]:
    kind = body.get("kind")
    if kind in _UNIMPLEMENTED_JOBS:
        return 501, {"error": f"未实现：{_UNIMPLEMENTED_JOBS[kind]}", "kind": kind}
    builder = _JOB_BUILDERS.get(kind)
    if not builder:
        return 400, {"error": f"未知任务类型：{kind}"}
    part, vol, ch = body.get("part"), body.get("vol"), body.get("ch")
    if part is None or vol is None or ch is None:
        return 400, {"error": "缺少 part/vol/ch"}
    id_key = f"{part}/{vol}/{ch}"
    dedupe = f"{kind}:{id_key}"
    with _RUNNING_LOCK:
        if dedupe in _RUNNING_KEYS:
            return 409, {"error": "同一任务正在进行，请等它结束"}
        _RUNNING_KEYS.add(dedupe)
    try:
        cmd, tmp_files = builder(novel_dir, part, vol, ch, body)
    except Exception as e:  # noqa: BLE001
        with _RUNNING_LOCK:
            _RUNNING_KEYS.discard(dedupe)
        return 400, {"error": str(e)}
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, cmd=cmd, cwd=str(paths.REPO_ROOT), tmp_files=tmp_files)
    job.id_key = id_key
    with _JOBS_LOCK:
        _JOBS[job.id] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return 200, {"job_id": job.id}


def _job_payload(job: Job) -> dict:
    return {"id": job.id, "kind": job.kind, "status": job.status, "returncode": job.returncode, "log": job.log}
