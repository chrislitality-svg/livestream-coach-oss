#!/usr/bin/env python3
"""自动采集守护：持续监控标杆直播间，补齐样本场次。

用法:
  python scripts/auto_record.py                # 常驻
  python scripts/auto_record.py --once         # 盘点一轮就退出
  python scripts/auto_record.py --max 4        # 并发上限
"""
import argparse, json, pathlib, subprocess, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "douyin"
ROOMS = ROOT / "config" / "rooms.json"
PY = sys.executable

RETRY_HOURS = 2
MAX_PARALLEL = 6
MIN_MINUTES = 8

DAILY_PER_ROOM = 2
WATCH_MAX_PER_ROUND = 2
MIN_GAP_HOURS = 6
PAIR_EXTRA = 2


def rj(p, d=None):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return d


def room_stats():
    """每个采集候选房间：已完成场次数 n、上次采成功 last、上次尝试 last_try、今日已采 today。
    """
    cfg = (rj(ROOMS) or {}).get("rooms", {})
    bench = {rid: r for rid, r in cfg.items()
             if r.get("group") == "benchmark" or r.get("watch")}
    stats = {rid: {"name": r.get("name", rid), "category": r.get("category", ""),
                   "n": 0, "last": 0, "last_try": 0, "today": 0,
                   "watch_only": r.get("group") != "benchmark",
                   "daily": int(r.get("daily") or DAILY_PER_ROOM),
                   "pair": r.get("pair") or next(
                       (k for k, v in cfg.items() if v.get("pair") == rid), None)}
             for rid, r in bench.items()}
    today = time.strftime("%Y%m%d")
    for d in DATA.glob("*/*"):
        rid = d.parent.name
        if rid not in stats:
            continue
        try:
            stats[rid]["last_try"] = max(
                stats[rid]["last_try"],
                time.mktime(time.strptime(d.name, "%Y%m%d_%H%M%S")))
        except Exception:
            pass
        meta = rj(d / "meta.json")
        if not meta:
            continue
        if meta.get("duration_sec", 0) < MIN_MINUTES * 60:
            continue
        stats[rid]["n"] += 1
        if d.name.startswith(today):
            stats[rid]["today"] += 1
        try:
            last = time.mktime(time.strptime(meta["started_at"], "%Y-%m-%d %H:%M:%S"))
            stats[rid]["last"] = max(stats[rid]["last"], last + meta.get("duration_sec", 0))
        except Exception:
            pass
    return stats


async def is_live_batch(page, rid):
    """单房间在播检查（复用已打开的浏览器会话）。
    """
    try:
        await page.goto("https://live.douyin.com/" + rid,
                        wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(4000)
        return bool(await page.evaluate("""() => {
          const vs=[...document.querySelectorAll('video')];
          return !!vs.find(v => !isFinite(v.duration)
                             && (v.readyState >= 2 || v.videoWidth > 0));}"""))
    except Exception:
        return False


def candidates(max_pick):
    """选出本轮要采集的房间：场次少的优先。
    """
    stats = room_stats()
    now = time.time()
    cand = [(rid, v) for rid, v in sorted(stats.items(),
                                          key=lambda x: (x[1]["n"], x[0]))
            if v["today"] < v["daily"]
            and now - v["last"] > MIN_GAP_HOURS * 3600
            and now - v["last_try"] > RETRY_HOURS * 3600]

    keep, used = [], 0
    for rid, v in cand:
        if v.get("watch_only"):
            if used >= WATCH_MAX_PER_ROUND:
                continue
            used += 1
        keep.append((rid, v))
    cand = keep
    return cand[:max_pick], stats


def system_recording():
    """系统级在录房间清单（含手动/其他守护起的），防止重复起录。
    auto_record 只看自己的 Popen 会漏掉外部进程 —— 实测曾对同房间重起。
    """
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process "
         "-Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
         "Where-Object { $_.CommandLine -match 'record\\.py\\W+douyin' } | "
         "ForEach-Object { if ($_.CommandLine -match 'live\\.douyin\\.com/(\\d+)') "
         "{ $matches[1] } }"],
        capture_output=True, text=True)
    rooms = set()
    for ln in (out.stdout or "").splitlines():
        ln = ln.strip()
        if ln.isdigit():
            rooms.add(ln)
    return rooms


def reap_hung():
    """顺手收掉卡死的录制。
    """
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import cleanup_failed as cf
    except Exception as e:
        print("  卡死检查不可用：%s" % str(e)[:80], flush=True)
        return
    try:
        hung = []
        for pid, rid, age in cf.recording_procs():
            sd = cf.session_of(rid)
            audio = (sd / "audio.webm") if sd else None
            size = audio.stat().st_size if audio and audio.exists() else 0
            if age > cf.HUNG_MINUTES and size == 0:
                hung.append((pid, rid, age))
        for pid, rid, age in hung:
            print("  清理卡死录制 %s（pid %d，已跑 %.0f 分钟、0 字节）"
                  % (rid, pid, age), flush=True)
        if hung:
            cf.kill([p for p, _, _ in hung], True)
            time.sleep(2)
            import shutil
            for _, rid, _ in hung:
                sd = cf.session_of(rid)
                if not sd or not sd.exists():
                    continue
                audio = sd / "audio.webm"
                if audio.exists() and audio.stat().st_size > 0:
                    continue
                tgt = ROOT / "data" / "_failed" / rid / sd.name
                try:
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(sd), str(tgt))
                    print("  空目录已隔离 %s/%s" % (rid, sd.name), flush=True)
                except Exception as e:
                    print("  隔离空目录失败：%s" % str(e)[:60], flush=True)

        alive = {rid for _, rid, _ in cf.recording_procs()}
        orphan = [p for rid, pids in cf.profile_browsers().items()
                  if rid not in alive for p in pids]
        if orphan:
            print("  清理孤儿浏览器 %d 个进程" % len(orphan), flush=True)
            cf.kill(orphan, True)
    except Exception as e:
        print("  卡死清理失败：%s" % str(e)[:80], flush=True)


def run_once(max_parallel, active):
    """一轮盘点：先收卡死的，再按缺额补在播候选（并发按系统级在录数算）。"""
    active[:] = [p for p in active if p.poll() is None]
    reap_hung()
    busy = system_recording()
    free = max_parallel - len(busy)
    if free <= 0:
        print("[%s] %d 路在录（系统级），满员" % (time.strftime("%H:%M"), len(busy)),
              flush=True)
        return
    cand, stats = candidates(free)
    cand = [(rid, c) for rid, c in cand if rid not in busy]
    if not cand:
        print("[%s] 无候选（全部在冷却期内、已达标或在录中）" % time.strftime("%H:%M"),
              flush=True)
        return
    print("[%s] 候选 %d: %s" % (time.strftime("%H:%M"), len(cand),
          "、".join(c["name"] for _, c in cand)), flush=True)

    from playwright.async_api import async_playwright
    profile = str(ROOT / ".profiles" / "douyin")

    async def check():
        picked = []
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=profile, headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 820})
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            for rid, c in cand:
                if len(picked) >= free:
                    break
                if any(rid == x for x, _ in picked):
                    continue
                if await is_live_batch(page, rid):
                    picked.append((rid, c))
                    print("  %s 在播 ✓" % c["name"], flush=True)
                else:
                    print("  %s 下播" % c["name"], flush=True)
                    continue
                pr = c.get("pair")
                pc = stats.get(pr) if pr else None
                if (pc and pr not in busy and len(picked) < free
                        and not any(pr == x for x, _ in picked)
                        and pc["today"] < pc["daily"] + PAIR_EXTRA):
                    if await is_live_batch(page, pr):
                        picked.append((pr, pc))
                        print("  %s 在播 ✓（配对同录）" % pc["name"], flush=True)
            await ctx.close()
        return picked

    picked = asyncio_run(check())
    for rid, c in picked:
        logf = ROOT / "logs" / ("rec_%s.log" % rid)
        proc = subprocess.Popen(
            [PY, str(ROOT / "scripts" / "record.py"), "douyin",
             "https://live.douyin.com/" + rid, "--minutes", "60"],
            cwd=str(ROOT), stdout=open(logf, "a"), stderr=subprocess.STDOUT)
        active.append(proc)
        print("  启动录制 %s (%s)，pid %d" % (c["name"], rid, proc.pid), flush=True)


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


def main():
    from _proc import tee_log
    tee_log(ROOT / "logs" / "auto_record.log")
    ap = argparse.ArgumentParser(description="自动采集守护")
    ap.add_argument("--once", action="store_true", help="盘点一轮就退出")
    ap.add_argument("--max", type=int, default=MAX_PARALLEL, help="并发录制上限")
    ap.add_argument("--every", type=int, default=30, help="盘点间隔分钟")
    a = ap.parse_args()

    active = []
    print("自动采集守护启动（并发上限 %d，间隔 %d 分钟）" % (a.max, a.every), flush=True)
    while True:
        try:
            run_once(a.max, active)
        except Exception as e:
            print("[%s] 轮次异常：%s" % (time.strftime("%H:%M"), str(e)[:160]), flush=True)
        if a.once:
            break
        time.sleep(a.every * 60)


if __name__ == "__main__":
    main()
