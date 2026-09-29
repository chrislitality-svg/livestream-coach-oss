#!/usr/bin/env python3
"""给人读、给模型读的逐字稿从哪来。
"""
import json, pathlib


def read_items(d):
    """阅读版的句子 [{start, end, text}]；没有阅读版返回 None。"""
    p = pathlib.Path(d) / "transcript.read.json"
    if not p.exists():
        return None
    try:
        return [{"start": x["start"], "end": x["end"], "text": x["text"]}
                for x in json.loads(p.read_text(encoding="utf-8"))["items"] if x.get("text")]
    except Exception:
        return None


def reading_items(d):
    """人读 / 模型读用的句子：有阅读版用阅读版，否则用 Nano 的带标点文本。"""
    r = read_items(d)
    if r:
        return r
    tr = json.loads((pathlib.Path(d) / "transcript.json").read_text(encoding="utf-8"))
    return [{"start": x["start"], "end": x["end"], "text": x.get("punct") or x["text"]}
            for x in tr.get("items") or []]
