#!/usr/bin/env python3
"""评判标准的加载。标准写在 config/standard.md，由人审定。
"""
import pathlib

STD = pathlib.Path(__file__).resolve().parent.parent / "config" / "standard.md"
MARK = "【待确认】"


def load():
    """返回可以喂给模型的标准文本（只含已确认条目）。文件不存在返回空串。"""
    if not STD.exists():
        return ""
    text = STD.read_text(encoding="utf-8")
    a, b = text.find("## 一、"), text.find("## 四、")
    if a < 0:
        return ""
    body = text[a:b] if b > a else text[a:]
    return "\n".join(l for l in body.splitlines() if MARK not in l).strip()


def system_message():
    """包成 system 消息。规则放 system 比拼在 user 里权重更高、更不容易被忽略。"""
    s = load()
    if not s:
        return []
    return [{"role": "system", "content":
             "下面是评判标准，请按它来分析。\n\n" + s}]
