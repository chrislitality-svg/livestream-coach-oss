#!/usr/bin/env python3
"""把 transcript.json 导出成可读逐字稿。

用法:
  python scripts/export_transcript.py <录制目录>
  python scripts/export_transcript.py <录制目录> --merge 30   # 每30秒合并成一段
"""
import argparse, json, pathlib, sys
from _reading import read_items, reading_items

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def mmss(s):
    return "%02d:%02d" % (int(s) // 60, int(s) % 60)


def main():
    ap = argparse.ArgumentParser(description="导出逐字稿")
    ap.add_argument("recdir")
    ap.add_argument("--merge", type=float, default=0,
                    help="按秒数合并成段落，0 为逐句输出")
    a = ap.parse_args()
    d = pathlib.Path(a.recdir).expanduser()
    tr = json.loads((d / "transcript.json").read_text(encoding="utf-8"))
    items = reading_items(d)

    lines = ["# 直播逐字稿", ""]
    lines.append("时长 %s ・ %d 句 ・ %d 字 ・ 语速 %s 字/分 ・ 转写 %s/%s"
                 % (mmss(tr["audio_sec"]), len(items), tr["chars"],
                    tr["chars_per_min"], tr.get("engine"), tr.get("model")))
    if read_items(d):
        lines.append("正文为阅读版（Qwen3-ASR + 热词），上面的字数、语速按 Nano 转写统计")
    lines.append("")

    if a.merge:
        buf, t0 = [], None
        for it in items:
            if t0 is None:
                t0 = it["start"]
            buf.append(it.get("punct") or it["text"])
            if it["end"] - t0 >= a.merge:
                lines.append("**[%s]** %s" % (mmss(t0), "".join(buf)))
                lines.append("")
                buf, t0 = [], None
        if buf:
            lines.append("**[%s]** %s" % (mmss(t0), "".join(buf)))
    else:
        for it in items:
            lines.append("`[%s]` %s" % (mmss(it["start"]), it.get("punct") or it["text"]))

    p = d / "逐字稿.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print("已导出 -> %s（%d 句 / %d 字）" % (p, len(items), tr["chars"]))


if __name__ == "__main__":
    main()
