#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
状态树写锁 (state_lock.py) —— W6.2

`05_工作区/02_状态/` 下的 `01_最新状态/` 是全书状态的**唯一权威存储**，由
`merge_chapter_state.py` / `rebuild_global_state.py` / `build_state_snapshot.py`
覆盖写入。三者都是「读全量 → 算 → 全量重写」，**没有任何互斥**：两个进程同时跑，
后写的会把先写的整棵树盖掉，而且 prune 步骤会删掉对方刚建的对象文件——
结果不是报错，是**静默的状态丢失**。

本仓已经有过并行会话互相踩的教训（另一个会话 `git add -A` 把未提交的新文件
一起卷走）。写锁把这类事故从「事后才发现」变成「当场拒绝」。

用法
----
    from state_lock import state_write_lock

    with state_write_lock(state_root, tool="merge_chapter_state.py"):
        ...  # 写状态树

锁文件 `<state_root>/.state.lock`（已 gitignore）记 pid / 主机 / 工具 / 起始时间。
- 同机且持锁进程还活着 → 直接拒绝，打印是谁占着。
- 持锁进程已死（或跨机且超过 `stale_after` 秒）→ 判为**残锁**，打印警告后接管。
  残锁通常来自被 Ctrl-C 或崩掉的上一次运行。
- `--no-lock` 之类的旁路**不提供**：需要并行时应当排队，而不是关掉互斥。
"""
import atexit
import errno
import json
import os
import socket
import time
from contextlib import contextmanager

LOCK_FILENAME = ".state.lock"
DEFAULT_STALE_AFTER = 30 * 60      # 跨机残锁判定阈值（秒）


class StateLockError(Exception):
    """拿不到锁。上层据此中止，不写任何文件。"""


def _pid_alive(pid: int) -> bool:
    """本机进程是否还在。跨机无法判断，调用方走时间阈值。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                 # 存在但不属于当前用户
    return True


def _read_lock(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _describe(info: dict) -> str:
    started = info.get("started", "?")
    age = ""
    try:
        age = f"，已持有 {int(time.time() - float(info.get('epoch', 0))) // 60} 分钟"
    except (TypeError, ValueError):
        pass
    return (f"{info.get('tool', '未知工具')}"
            f"（pid {info.get('pid', '?')} @ {info.get('host', '?')}，起于 {started}{age}）")


def _is_stale(info: dict, stale_after: int) -> tuple[bool, str]:
    if not info:
        return True, "锁文件损坏或为空"
    same_host = info.get("host") == socket.gethostname()
    if same_host:
        if not _pid_alive(int(info.get("pid", -1) or -1)):
            return True, "持锁进程已不存在（多半是上次运行被中断）"
        return False, ""
    try:
        age = time.time() - float(info.get("epoch", 0))
    except (TypeError, ValueError):
        return True, "锁文件时间戳不可解析"
    if age > stale_after:
        return True, f"跨机锁且已超过 {stale_after // 60} 分钟未释放"
    return False, ""


@contextmanager
def state_write_lock(state_root, tool: str, stale_after: int = DEFAULT_STALE_AFTER,
                     verbose: bool = True):
    """独占状态树写权限。拿不到就抛 `StateLockError`，**不要**吞掉它继续写。"""
    state_root = str(state_root)
    os.makedirs(state_root, exist_ok=True)
    path = os.path.join(state_root, LOCK_FILENAME)
    payload = json.dumps({
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "tool": tool,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": time.time(),
    }, ensure_ascii=False)

    acquired = False
    for attempt in (1, 2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            acquired = True
            break
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
            info = _read_lock(path)
            stale, why = _is_stale(info, stale_after)
            if not stale:
                raise StateLockError(
                    f"状态树正被占用：{_describe(info)}。\n"
                    f"  同时写会让后写的整棵树盖掉先写的（prune 还会删掉对方刚建的对象文件），"
                    f"因此这里直接中止、不写任何文件。\n"
                    f"  等它跑完再来；确认那个进程已经没了，删掉 "
                    f"`{os.path.join(state_root, LOCK_FILENAME)}` 即可。")
            if attempt == 1:
                if verbose:
                    print(f"[state_lock] 接管残锁：{_describe(info)}——{why}")
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass            # 别人抢先清了，下一轮重试
            else:
                raise StateLockError(f"清掉残锁后仍拿不到锁，疑似有并发进程正在反复抢：{path}")

    if not acquired:                # 理论上到不了；防御性
        raise StateLockError(f"未能获得状态树写锁：{path}")

    try:
        yield path
    finally:
        try:
            # 只删自己写的那把锁，避免误删别人接管后新建的
            if _read_lock(path).get("pid") == os.getpid():
                os.unlink(path)
        except FileNotFoundError:
            pass


# ── 供 CLI 工具用的「持到进程退出」形态 ────────────────────────────

def acquire_until_exit(state_root, tool: str, stale_after: int = DEFAULT_STALE_AFTER,
                       verbose: bool = True) -> str:
    """拿锁并注册退出时释放，返回锁文件路径。拿不到抛 `StateLockError`。

    三个写状态树的 CLI 都是「读全量 → 算 → 全量重写」的短命进程，危险窗口是
    **整个读-算-写**，不只是写那一下。持到进程退出正好覆盖这个窗口，
    而且调用方不必为了套 `with` 把整个 main 重新缩进。
    """
    cm = state_write_lock(state_root, tool, stale_after=stale_after, verbose=verbose)
    path = cm.__enter__()
    atexit.register(lambda: cm.__exit__(None, None, None))
    return path
