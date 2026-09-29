#!/usr/bin/env python3
"""常驻守护：录制一结束就自动转写分析出报告。

用法:
  python scripts/auto_analyze.py                 # 常驻
  python scripts/auto_analyze.py --once          # 把当前积压的跑完就退出
"""
import argparse, json, pathlib, subprocess, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PY = sys.executable

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def pending():
    if not DATA.exists():
        return []
    out = []
    for d in sorted(DATA.glob("*/*/*")):
        if not d.is_dir():
            continue
        if (d / "meta.json").exists() and not (d / "analysis.json").exists() \
                and (d / "audio.webm").exists():
            out.append(d)
    return out


MIN_SEC = 8 * 60
LLM_FILES = ("deep.json", "rounds.json", "visual.json")
BACKFILL_EVERY = 600
PROBE_BACKOFF = 1800
MAX_TRIES = 3
TRIES = ROOT / "logs" / ".llm_backfill.json"


PRODUCER = {"deep.json": "deep_analyze.py", "rounds.json": "rounds.py", "visual.json": "visual_metrics.py"}


def llm_files():
    """这台机器应该有的模型层产物：没配视觉模型（_llm.vl_model() 为空）就不等 visual.json；
    发行版里没有的模块也不等。
    """
    import _llm, _config
    return [f for f in LLM_FILES if (f != "visual.json" or _llm.vl_model())
            and _config.has_script(PRODUCER[f])]


def rj(p):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def missing_llm():
    """缺模型层的已分析场次：我方和重点盯的房间排前面，其余新场次优先。"""
    rooms = (rj(ROOT / "config" / "rooms.json") or {}).get("rooms", {})
    out = []
    for d in DATA.glob("*/*/*"):
        if not (d / "analysis.json").exists():
            continue
        if ((rj(d / "meta.json") or {}).get("duration_sec") or 0) < MIN_SEC:
            continue
        if all((d / f).exists() for f in llm_files()):
            continue
        r = rooms.get(d.parent.name, {})
        out.append((not (r.get("group") == "own" or r.get("watch")), -d.stat().st_mtime, d))
    return [d for *_, d in sorted(out)]


def backfill(state, n):
    now = time.time()
    if now < state.get("next_check", 0):
        return
    state["next_check"] = now + BACKFILL_EVERY
    tries = rj(TRIES) or {}
    key = lambda d: "%s/%s" % (d.parent.name, d.name)
    todo = [d for d in missing_llm() if tries.get(key(d), 0) < MAX_TRIES]
    if not todo or now < state.get("probe_after", 0):
        return
    sys.path.insert(0, str(ROOT / "scripts"))
    import _llm
    ok, why = _llm.probe()
    if not ok:
        state["probe_after"] = now + PROBE_BACKOFF
        print("[%s] %d 场缺模型层，但模型服务不可用（%s），30 分钟后再探"
              % (time.strftime("%H:%M:%S"), len(todo), why[:80]), flush=True)
        return
    batch = todo[:n]
    print("[%s] 模型服务可用，补模型层 %d 场（共缺 %d 场）"
          % (time.strftime("%H:%M:%S"), len(batch), len(todo)), flush=True)
    run_batch(batch, ["--llm-only"])
    for d in batch:
        if all((d / f).exists() for f in llm_files()):
            tries.pop(key(d), None)
        else:
            tries[key(d)] = tries.get(key(d), 0) + 1
    try:
        TRIES.write_text(json.dumps(tries, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    state["next_check"] = 0


READ_EVERY = 300
BELOW_NORMAL = 0x00004000


def read_todo():
    rooms = ((rj(ROOT / "config" / "hotwords.json") or {}).get("rooms") or {}).keys()
    out = []
    for rid in rooms:
        for d in DATA.glob("*/%s/*" % rid):
            if (d / "analysis.json").exists() and not (d / "transcript.read.json").exists() \
                    and ((rj(d / "meta.json") or {}).get("duration_sec") or 0) >= MIN_SEC:
                out.append(d)
    return sorted(out, key=lambda d: d.name, reverse=True)


def read_tick(state):
    p = state.get("read_proc")
    if p and p.poll() is None:
        return
    tries = rj(TRIES) or {}
    if p:
        state["read_fh"].close()
        d = state["read_dir"]
        k = "read:%s/%s" % (d.parent.name, d.name)
        if not (d / "transcript.read.json").exists() and p.returncode != 3:
            tries[k] = tries.get(k, 0) + 1
            print("  [阅读版失败] %s/%s 退出码 %s" % (d.parent.name, d.name, p.returncode), flush=True)
            try:
                TRIES.write_text(json.dumps(tries, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass
        state["read_proc"] = None
    now = time.time()
    if now < state.get("read_next", 0):
        return
    state["read_next"] = now + READ_EVERY
    if not (ROOT / "scripts" / "read_asr.py").exists():
        return
    todo = [d for d in read_todo() if tries.get("read:%s/%s" % (d.parent.name, d.name), 0) < MAX_TRIES]
    if not todo:
        return
    d = todo[0]
    fh = open(ROOT / "logs" / ("read_asr_%s_%s.log" % (d.parent.name, d.name)), "a", encoding="utf-8")
    try:
        state["read_proc"] = subprocess.Popen(
            [PY, str(ROOT / "scripts" / "read_asr.py"), str(d)], cwd=str(ROOT),
            stdout=fh, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW | BELOW_NORMAL)
    except Exception as e:
        fh.close()
        print("  [阅读版] 起不来：%s" % str(e)[:80], flush=True)
        return
    state["read_fh"], state["read_dir"] = fh, d
    print("[%s] 阅读版开转 %s/%s（待转 %d 场）"
          % (time.strftime("%H:%M:%S"), d.parent.name, d.name, len(todo)), flush=True)


def run_batch(batch, extra=()):
    procs = []
    for d in batch:
        lf = ROOT / "logs" / ("analyze_%s_%s.log" % (d.parent.name, d.name))
        fh = open(lf, "a", encoding="utf-8")
        try:
            p = subprocess.Popen(
                [PY, str(ROOT / "scripts" / "auto_one.py"), str(d)] + list(extra),
                cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            fh.close()
            print("  [失败] 起不来 %s/%s：%s"
                  % (d.parent.name, d.name, str(e)[:80]), flush=True)
            continue
        procs.append((d, p, fh))
    for d, p, fh in procs:
        p.wait()
        fh.close()
        if p.returncode != 0:
            print("  [失败] %s/%s 退出码 %d，详见 logs/analyze_%s_%s.log"
                  % (d.parent.name, d.name, p.returncode,
                     d.parent.name, d.name), flush=True)


def main():
    ap = argparse.ArgumentParser(description="录制完成后自动分析")
    ap.add_argument("--once", action="store_true", help="处理完当前积压就退出")
    ap.add_argument("--every", type=int, default=60, help="轮询间隔秒")
    ap.add_argument("--concurrency", type=int, default=3, help="每轮并发分析几场，默认 3")
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT / "scripts"))
    from _proc import tee_log
    tee_log(ROOT / "logs" / "auto_analyze.log")

    print("自动分析守护已启动（轮询 %ds）" % a.every, flush=True)
    idle = 0
    state = {}
    while True:
        try:
            read_tick(state)
        except Exception as e:
            print("  [阅读版异常] %s" % str(e)[:120], flush=True)
        todo = pending()
        if todo:
            batch = todo[:a.concurrency]
            print("发现 %d 个待分析场次，本轮并发处理 %d 个" % (len(todo), len(batch)),
                  flush=True)
            run_batch(batch)
            idle = 0
        else:
            idle += 1
            if idle % 10 == 1:
                print("[%s] 暂无待分析场次" % time.strftime("%H:%M:%S"), flush=True)
            try:
                backfill(state, a.concurrency)
            except Exception as e:
                print("  [补跑异常] %s" % str(e)[:120], flush=True)
        if a.once:
            break
        time.sleep(a.every)


if __name__ == "__main__":
    main()
