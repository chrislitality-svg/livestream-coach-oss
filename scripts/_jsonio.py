#!/usr/bin/env python3
"""落盘相关的共用实现。
"""
import json, os, pathlib


def write_json(path, obj, indent=1):
    """原子写：先写同目录临时文件，再 os.replace 换过去。
    """
    path = pathlib.Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, path)
