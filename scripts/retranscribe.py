#!/usr/bin/env python3
"""全库统一转写口径：把已分析场次的转写全部换成 FunASR-Nano，并重跑确定性分析。

用法:
  python scripts/retranscribe.py            # 跑到全部完成；中断后再跑会接着做
  python scripts/retranscribe.py --dry      # 只列出要处理的场次
"""
import argparse, json, pathlib, subprocess, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
SC = ROOT / "scripts"
DATA = ROOT / "data" / "douyin"
PY = sys.executable
LOG = ROOT / "logs" / "retranscribe.log"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
GAP_SEC = 12


def log(msg):
    line = "[%s] %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def rj(p):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def busy_dirs():
    """正在被别的进程处理的场次目录（分析守护的 auto_one / transcribe 等），跳过它们。"""
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
                        "ForEach-Object { $_.CommandLine }"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=CREATE_NO_WINDOW)
    out = set()
    for ln in (r.stdout or "").splitlines():
        if "retranscribe.py" in ln:
            continue
        for part in ln.replace('"', " ").split():
            if "douyin" in part and ("_" in part):
                out.add(part.replace("/", "\\").rstrip("\\").lower())
    return out


def state(d):
    """返回这一场还差哪一步：'transcribe' / 'analyze' / None（已完成）。"""
    t = rj(d / "transcript.json") or {}
    if t.get("engine") != _config.asr_engine():
        return "transcribe"
    an = d / "analysis.json"
    if not an.exists() or an.stat().st_mtime < (d / "transcript.json").stat().st_mtime:
        return "analyze"
    return None


def todo():
    rooms = (rj(ROOT / "config" / "rooms.json") or {}).get("rooms", {})
    rows = []
    for m in DATA.glob("*/*/meta.json"):
        d = m.parent
        if not (d / "analysis.json").exists():
            continue
        if not (d / "audio.webm").exists():
            continue
        st = state(d)
        if st:
            g = rooms.get(d.parent.name, {}).get("group")
            rows.append((0 if g == "benchmark" else 1, d.name, d, st))
    rows.sort()
    return [(d, st) for _, _, d, st in rows]


def run(step, args):
    r = subprocess.run([PY, str(SC / step)] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT),
                       creationflags=CREATE_NO_WINDOW)
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    return r.returncode, (tail[-1][:160] if tail else "")


def one(d, st):
    rel = "%s/%s" % (d.parent.name, d.name)
    if st == "transcribe":
        old = rj(d / "transcript.json") or {}
        eng = old.get("engine", "unknown")
        bak = d / ("transcript.%s.json" % eng)
        if (d / "transcript.json").exists() and not bak.exists():
            bak.write_bytes((d / "transcript.json").read_bytes())
        t0 = time.time()
        rc, tail = run("transcribe.py", [str(d), "--engine", _config.asr_engine()])
        if rc != 0:
            if bak.exists():
                (d / "transcript.json").write_bytes(bak.read_bytes())
            log("  失败 %s 转写：%s（已恢复原 %s 转写）" % (rel, tail, eng))
            return False
        log("  %s 转写完成（原 %s，%.0f 秒）" % (rel, eng, time.time() - t0))
    for step in ("analyze.py", "talk_metrics.py", "report.py"):
        rc, tail = run(step, [str(d)])
        if rc != 0:
            log("  失败 %s %s：%s" % (rel, step, tail))
            return False
    return True


def main():
    ap = argparse.ArgumentParser(description="全库统一转写口径到 FunASR-Nano")
    ap.add_argument("--dry", action="store_true", help="只列出要处理的场次")
    a = ap.parse_args()
    items = todo()
    total = sum((rj(d / "meta.json") or {}).get("duration_sec", 0) for d, _ in items)
    log("待处理 %d 场，%.1f 小时音频" % (len(items), total / 3600))
    if a.dry:
        for d, st in items[:20]:
            print("  %-12s %s/%s" % (st, d.parent.name, d.name))
        return 0
    ok = fail = skip = 0
    for i, (d, st) in enumerate(items, 1):
        if str(d).lower().rstrip("\\") in busy_dirs():
            skip += 1
            log("[%d/%d] 跳过 %s/%s（正被别的进程处理）" % (i, len(items), d.parent.name, d.name))
            continue
        st = state(d)
        if not st:
            continue
        log("[%d/%d] %s/%s" % (i, len(items), d.parent.name, d.name))
        if one(d, st):
            ok += 1
        else:
            fail += 1
        time.sleep(GAP_SEC)
    left = len(todo())
    log("结束：完成 %d / 失败 %d / 跳过 %d；仍待处理 %d 场%s" % (
        ok, fail, skip, left, "（再跑一次即可接着做）" if left else "，全库口径已统一"))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
