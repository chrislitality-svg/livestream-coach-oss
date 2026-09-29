#!/usr/bin/env python3
"""采集故障清理：收掉卡死的录制、占锁的孤儿浏览器、没产出的空目录。
"""
import argparse, json, pathlib, re, shutil, subprocess, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "douyin"
FAILED = ROOT / "data" / "_failed"
PROFILES = ROOT / ".profiles"

HUNG_MINUTES = 15


def ps(cmd):
    """跑一段 PowerShell，返回输出行。"""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                             capture_output=True, text=True, timeout=120)
        return [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
    except Exception as e:
        print("PowerShell 调用失败：%s" % str(e)[:100])
        return []


def recording_procs():
    """在跑的 record.py 进程：[(pid, 房间号, 已运行分钟)]。
    """
    lines = ps(
        "Get-CimInstance Win32_Process "
        "-Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -match 'record\\.py\\W+douyin' } | "
        "ForEach-Object { \"$($_.ProcessId)|$($_.CreationDate.ToString('yyyyMMddHHmmss'))|"
        "$($_.CommandLine)\" }")
    out = []
    for ln in lines:
        parts = ln.split("|", 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        m = re.search(r"live\.douyin\.com/(\d+)", parts[2])
        if not m:
            continue
        try:
            age = (time.time() - time.mktime(
                time.strptime(parts[1], "%Y%m%d%H%M%S"))) / 60
        except Exception:
            age = 0
        out.append((int(parts[0]), m.group(1), age))
    return out


def profile_browsers():
    """占着本项目 profile 的 chrome 主进程：{房间号: [pid...]}。"""
    lines = ps(
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -match 'livestream-coach..profiles' } | "
        "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }")
    out = {}
    for ln in lines:
        pid, _, cmd = ln.partition("|")
        if not pid.isdigit():
            continue
        m = re.search(r"douyin_(\d+)", cmd)
        if m:
            out.setdefault(m.group(1), []).append(int(pid))
    return out


def empty_sessions():
    """没落下任何数据的场次目录。"""
    out = []
    for sd in sorted(DATA.glob("*/*")):
        if not sd.is_dir():
            continue
        audio = sd / "audio.webm"
        if audio.exists() and audio.stat().st_size > 0:
            continue
        if (sd / "frames").exists() and any((sd / "frames").glob("*")):
            continue
        out.append(sd)
    return out


def session_of(rid):
    """某房间最新的场次目录，用来判断在跑的进程有没有产出。"""
    dirs = sorted((DATA / rid).glob("*")) if (DATA / rid).exists() else []
    return dirs[-1] if dirs else None


def kill(pids, apply):
    if not pids:
        return 0
    if not apply:
        return len(pids)
    args = ["taskkill", "/F", "/T"]
    for p in pids:
        args += ["/PID", str(p)]
    try:
        subprocess.run(args, capture_output=True, text=True, timeout=120)
    except Exception as e:
        print("  taskkill 失败：%s" % str(e)[:100])
        return 0
    return len(pids)


def main():
    ap = argparse.ArgumentParser(description="采集故障清理")
    ap.add_argument("--apply", action="store_true", help="真正执行，默认只报告")
    a = ap.parse_args()
    tag = "" if a.apply else "（预演，加 --apply 才会真的执行）"
    print("=== 采集故障清理 %s%s ===\n" % (time.strftime("%H:%M:%S"), tag))

    procs = recording_procs()
    hung, alive = [], set()
    for pid, rid, age in procs:
        sd = session_of(rid)
        audio = (sd / "audio.webm") if sd else None
        size = audio.stat().st_size if audio and audio.exists() else 0
        if age > HUNG_MINUTES and size == 0:
            hung.append((pid, rid, age))
        else:
            alive.add(rid)
    print("在跑的录制进程 %d 个，其中卡死 %d 个" % (len(procs), len(hung)))
    for pid, rid, age in hung:
        print("  卡死 pid=%d 房间 %s 已跑 %.0f 分钟，0 字节" % (pid, rid, age))
    n = kill([p for p, _, _ in hung], a.apply)
    if hung:
        print("  %s %d 个卡死进程\n" % ("已终止" if a.apply else "将终止", n))

    browsers = profile_browsers()
    orphan = []
    for rid, pids in browsers.items():
        if rid in alive:
            continue
        orphan += pids
    print("持有本项目 profile 的 chrome 进程覆盖 %d 个房间，孤儿进程 %d 个"
          % (len(browsers), len(orphan)))
    for rid, pids in sorted(browsers.items()):
        mark = "录制中，保留" if rid in alive else "孤儿，清理"
        print("  房间 %s：%d 个进程 —— %s" % (rid, len(pids), mark))
    n = kill(orphan, a.apply)
    if orphan:
        print("  %s %d 个孤儿进程\n" % ("已终止" if a.apply else "将终止", n))

    empties = empty_sessions()
    print("空场次目录 %d 个" % len(empties))
    from collections import Counter
    for rid, cnt in Counter(sd.parent.name for sd in empties).most_common():
        print("  房间 %s：%d 个" % (rid, cnt))
    if empties and a.apply:
        ok = 0
        for sd in empties:
            tgt = FAILED / sd.parent.name / sd.name
            try:
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(sd), str(tgt))
                ok += 1
            except Exception as e:
                print("  移动失败 %s：%s" % (sd.name, str(e)[:80]))
        print("  已移入 data/_failed/ 共 %d 个" % ok)
    elif empties:
        print("  将移入 data/_failed/（不删除，保留失败痕迹）")

    print("\n完成。%s" % ("建议接着重启采集守护：python scripts/install_services.py"
                          if a.apply else "确认无误后加 --apply 执行。"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
