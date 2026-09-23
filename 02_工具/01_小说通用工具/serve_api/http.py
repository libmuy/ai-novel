# -*- coding: utf-8 -*-
"""HTTP Handler、路由分发、`make_handler()`。"""
import json
import re
import sys
import time
import tomllib
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from prompt_build import layout as L  # noqa: E402

from . import feed as feed_mod
from . import files, jobs, model, paths, payload

_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/nocturne.css": ("nocturne.css", "text/css; charset=utf-8"),
}


def _qint(qs, key):
    v = qs.get(key, [None])[0]
    if v in (None, "", "null"):
        return None
    try:
        return int(v)
    except ValueError:
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "serve_audio/3.0"
    protocol_version = "HTTP/1.1"
    novel_dir: Path = None
    title: str = ""
    base_override: str | None = None
    read_only: bool = False

    def log_message(self, fmt, *a):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % a))

    def _send(self, body: bytes, ctype: str, code=200, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code)

    def _err(self, code, msg):
        self._json({"error": msg}, code)

    def do_HEAD(self):
        self.do_GET()

    def _base(self, u) -> str:
        if self.base_override:
            return self.base_override.rstrip("/")
        host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        return f"http://{host}"

    # ---------------------------------------------------------- GET

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        qs = urllib.parse.parse_qs(u.query)
        try:
            if path == "/health":
                return self._send(b"ok", "text/plain; charset=utf-8")
            if path in _STATIC_FILES:
                name, ctype = _STATIC_FILES[path]
                f = paths.UI_DIR / name
                if not f.is_file():
                    return self._err(404, "not found")
                self._send(f.read_bytes(), ctype)
                return
            if path == "/feed.xml":
                entries = model.scan(self.novel_dir)
                return self._send(feed_mod.render_feed(entries, self.title, self._base(u)),
                                  "application/rss+xml; charset=utf-8")
            if path.startswith("/audio/"):
                return self._serve_audio(path[len("/audio/"):].split("/"), model.scan(self.novel_dir))
            if path.startswith("/api/"):
                return self._route_api_get(path, qs)
            self._err(404, "not found")
        except BrokenPipeError:
            pass
        except Exception as ex:  # noqa: BLE001
            self._err(500, str(ex))

    def _route_api_get(self, path, qs):
        novel_dir = self.novel_dir
        if path == "/api/config":
            return self._json(self._config_payload())
        if path == "/api/book":
            tree, _entries = model._scan_tree(novel_dir)
            return self._json(payload._book_payload(novel_dir, self.title, self._base(urllib.parse.urlparse(self.path)), tree))
        if path == "/api/level":
            sec = qs.get("sec", ["work"])[0]
            part, vol, ch = _qint(qs, "part"), _qint(qs, "vol"), _qint(qs, "ch")
            tree, entries = model._scan_tree(novel_dir)
            code, data = payload._level_payload(novel_dir, sec, part, vol, ch, self.title, tree, entries)
            return self._json(data, code)
        if path == "/api/file":
            rel = qs.get("path", [None])[0]
            return self._get_file(rel)
        if path == "/api/backfill-targets":
            part, vol, ch = _qint(qs, "part"), _qint(qs, "vol"), _qint(qs, "ch")
            tree, _entries = model._scan_tree(novel_dir)
            try:
                targets = payload._backfill_targets(novel_dir, part, vol, ch, tree)
            except L.LayoutError as e:
                return self._err(400, str(e))
            return self._json({"targets": targets})
        if path.startswith("/api/jobs/"):
            job_id = path[len("/api/jobs/"):]
            with jobs._JOBS_LOCK:
                job = jobs._JOBS.get(job_id)
            if not job:
                return self._err(404, "任务不存在")
            return self._json(jobs._job_payload(job))
        self._err(404, "not found")

    def _config_payload(self) -> dict:
        llm = {"base_url": "", "model": ""}
        try:
            cfg = tomllib.loads((paths.SYS_DIR / "llm.config.toml").read_text(encoding="utf-8"))
            llm = {"base_url": cfg.get("base_url", ""), "model": cfg.get("model", "")}
        except OSError:
            pass
        return {"read_only": self.read_only, "voices": jobs._VOICES, "llm": llm}

    def _get_file(self, rel_path):
        if not rel_path:
            return self._err(400, "缺少 path")
        try:
            p = files._safe_resolve(self.novel_dir, rel_path)
        except FileNotFoundError:
            return self._err(404, "not found")
        except ValueError as e:
            return self._err(400, str(e))
        if p.suffix.lower() == ".mp3":
            return self._json({"path": rel_path, "name": p.name, "kind": "audio",
                                "size": model._fmt_size(model._safe_size(p)), "audio_url": model._audio_url_for(p) or ""})
        if p.suffix.lower() not in files._TEXT_EXT:
            return self._json({"path": rel_path, "name": p.name, "kind": "binary",
                                "size": model._fmt_size(model._safe_size(p))})
        raw = p.read_bytes()
        truncated = len(raw) > files._RENDER_CAP
        text = raw[:files._RENDER_CAP].decode("utf-8", "replace")
        return self._json({"path": rel_path, "name": p.name, "kind": files._file_kind(p),
                            "text": text, "truncated": truncated, "size": model._fmt_size(len(raw))})

    def _serve_audio(self, rest, entries):
        ref = model._parse_ref("/".join(rest))
        if not ref:
            return self._err(404, "not found")
        part, vol, ch, scene = ref
        e = model.find(entries, part, vol, ch)
        path = e.unit_by_scene(scene) if e else None
        if not path or not path.exists():
            return self._err(404, "not found")
        self._stream(path)

    def _stream(self, path: Path):
        size = model._safe_size(path)
        rng = self.headers.get("Range")
        start, end, partial = 0, size - 1, False
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                partial = True
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # ---------------------------------------------------------- 写 / 任务

    def _check_write_allowed(self) -> bool:
        if self.read_only:
            self._err(403, "只读模式：服务端已禁用写入")
            return False
        if self.headers.get("X-Review-UI") != "1":
            self._err(403, "缺少 X-Review-UI 请求头")
            return False
        origin = self.headers.get("Origin")
        if origin:
            host = self.headers.get("Host", "")
            origin_host = urllib.parse.urlparse(origin).netloc
            if origin_host and origin_host != host:
                self._err(403, "Origin 与 Host 不匹配")
                return False
        return True

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw) if raw else {}

    def do_PUT(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if not path.startswith("/api/"):
            return self._err(404, "not found")
        if not self._check_write_allowed():
            return
        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            return self._err(400, "请求体不是合法 JSON")
        try:
            if path == "/api/file":
                return self._put_file(body)
            self._err(404, "not found")
        except (ValueError, L.LayoutError) as e:
            self._err(400, str(e))
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def _put_file(self, body):
        rel_path = body.get("path", "")
        text = body.get("text", "")
        orig = body.get("orig") or rel_path
        overwrite = bool(body.get("overwrite"))
        target = files._validate_rel_path(self.novel_dir, rel_path)
        if rel_path != orig and target.exists() and not overwrite:
            return self._json({"error": f"{rel_path} 已存在，未覆盖"}, 409)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)
        self._json({"ok": True, "path": files._rel(self.novel_dir, target), "size": model._fmt_size(model._safe_size(target))})

    def do_DELETE(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        qs = urllib.parse.parse_qs(u.query)
        if not path.startswith("/api/"):
            return self._err(404, "not found")
        if not self._check_write_allowed():
            return
        try:
            if path == "/api/file":
                return self._delete_file(qs.get("path", [None])[0])
            self._err(404, "not found")
        except FileNotFoundError:
            self._err(404, "not found")
        except ValueError as e:
            self._err(400, str(e))
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def _delete_file(self, rel_path):
        if not rel_path:
            return self._err(400, "缺少 path")
        target = files._safe_resolve(self.novel_dir, rel_path)
        ts = time.strftime("%Y%m%d_%H%M%S")
        dest = self.novel_dir / ".trash" / ts / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        target.rename(dest)
        self._json({"ok": True, "trashed_to": files._rel(self.novel_dir, dest)})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        if not path.startswith("/api/"):
            return self._err(404, "not found")
        if not self._check_write_allowed():
            return
        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            return self._err(400, "请求体不是合法 JSON")
        try:
            if path == "/api/backfill":
                return self._post_backfill(body)
            if path == "/api/jobs":
                code, data = jobs._create_job(self.novel_dir, body)
                return self._json(data, code)
            self._err(404, "not found")
        except (ValueError, L.LayoutError) as e:
            self._err(400, str(e))
        except FileNotFoundError:
            self._err(404, "not found")
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def _post_backfill(self, body):
        part, vol, ch = body.get("part"), body.get("vol"), body.get("ch")
        tree, _entries = model._scan_tree(self.novel_dir)
        result = payload._do_backfill(self.novel_dir, body.get("src", ""), body.get("target_id", ""),
                                      part, vol, ch, tree)
        if body.get("cold") and result.get("mode") in ("outline", "manuscript"):
            job_body = {"kind": "cold", "part": part, "vol": vol, "ch": ch,
                        "mode": result["mode"], "engine": body.get("engine", "local"),
                        "oc_models": body.get("oc_models")}
            code, job_data = jobs._create_job(self.novel_dir, job_body)
            if code == 200:
                result["job_id"] = job_data["job_id"]
            else:
                result["job_error"] = job_data.get("error")
        self._json(result)


def make_handler(novel_dir: Path, title: str, base_override: str | None, read_only: bool = False):
    return type("BoundHandler", (Handler,),
                {"novel_dir": novel_dir, "title": title, "base_override": base_override,
                 "read_only": read_only})
