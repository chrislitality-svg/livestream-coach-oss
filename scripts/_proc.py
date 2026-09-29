#!/usr/bin/env python3
"""跨平台子进程工具：确保后台/守护进程拉起的子进程不弹控制台窗口。

用法：
    from _proc import run_quiet, popen_quiet
    run_quiet([PY, "x.py"])
    popen_quiet([PY, "x.py"])
或直接用常量：
    subprocess.run(cmd, creationflags=CREATE_NO_WINDOW)
"""
from __future__ import annotations

import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _kw(extra: dict | None = None) -> dict:
    kw = {"creationflags": CREATE_NO_WINDOW}
    if extra:
        kw.update(extra)
    return kw


def run_quiet(cmd, **kw):
    """subprocess.run，隐藏控制台窗口。"""
    return subprocess.run(cmd, **_kw(kw))


def popen_quiet(cmd, **kw):
    """subprocess.Popen，隐藏控制台窗口；默认丢弃 stdout/stderr 防止写管道阻塞。"""
    kw.setdefault("stdout", subprocess.DEVNULL)
    kw.setdefault("stderr", subprocess.DEVNULL)
    return subprocess.Popen(cmd, **_kw(kw))


class _Tee:
    def __init__(self, f):
        self.f = f

    def write(self, b):
        if sys.__stdout__ is not None:
            try:
                sys.__stdout__.write(b)
            except Exception:
                pass
        self.f.write(b)
        self.f.flush()

    def flush(self):
        self.f.flush()


def tee_log(path):
    """守护进程把 print 同时写进日志文件。
    """
    import pathlib
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = _Tee(open(p, "a", encoding="utf-8"))
