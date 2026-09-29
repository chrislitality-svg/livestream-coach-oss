#!/usr/bin/env python3
"""全库重做转写后处理：同音纠错 + 标点 → 重算规则层 → 话术量化 → 重出报告。

用法:
  python scripts/post_all.py            # 只处理还没做过后处理的场次（没有 term_fixes 字段）
  python scripts/post_all.py --all      # 全部重做（改了纠错表后用这个）
  python scripts/post_all.py --all --workers 2
"""
import argparse, json, os, pathlib, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
STEPS = [s for s in [["transcribe.py", "--post-only"], ["analyze.py"], ["talk_metrics.py"], ["commerce.py"], ["report.py"]]
         if (ROOT / "scripts" / s[0]).exists()]
BUSY_SEC = 180
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def todo(everything):
    out, now = [], time.time()
    for t in sorted((ROOT / "data").glob("*/*/*/transcript.json")):
        d = t.parent
        if not ((d / "analysis.json").exists() and (d / "meta.json").exists()):
            continue
        if now - (d / "analysis.json").stat().st_mtime < BUSY_SEC:
            continue
        if not everything:
            try:
                if "term_fixes" in json.loads(t.read_text(encoding="utf-8")):
                    continue
            except Exception:
                continue
        out.append(d)
    return out


def run(d):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    for step in STEPS:
        r = subprocess.run([PY, str(ROOT / "scripts" / step[0]), str(d)] + step[1:],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            return d, step[0], ((r.stderr or r.stdout).strip().splitlines() or [""])[-1]
    return d, None, None


def main():
    ap = argparse.ArgumentParser(description="全库重做转写后处理（纠错 + 标点）并重算规则层")
    ap.add_argument("--all", action="store_true", help="全部重做（默认只做还没做过的）")
    ap.add_argument("--workers", type=int, default=3, help="并行几路（默认 3，给录制留 CPU）")
    a = ap.parse_args()
    ds = todo(a.all)
    print("待处理 %d 场" % len(ds), flush=True)
    t0, ok, bad = time.time(), 0, []
    with ThreadPoolExecutor(max(1, a.workers)) as ex:
        for d, step, err in ex.map(run, ds):
            if step:
                bad.append((d, step, err))
            else:
                ok += 1
    print("完成 %d 场，失败 %d，用时 %.0f 秒" % (ok, len(bad), time.time() - t0))
    for d, step, err in bad:
        print("失败 %s/%s 在 %s：%s" % (d.parent.name, d.name, step, err[:120]))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
