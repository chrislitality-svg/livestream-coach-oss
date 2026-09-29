#!/usr/bin/env python3
"""日更产出体检：回答"这一天系统到底产出了什么"。

用法:
  python scripts/health_check.py               # 体检一次并打印
  python scripts/health_check.py --days 7      # 看最近 7 天
  python scripts/health_check.py --daemon      # 常驻，每天出一次报告
  python scripts/health_check.py --quiet       # 只输出结论行（给推送用）
"""
import argparse, json, pathlib, re, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent

DAEMON_MODE = False
DATA = ROOT / "data" / "douyin"
FAILED = ROOT / "data" / "_failed"
LOGS = ROOT / "logs"
ROOMS = ROOT / "config" / "rooms.json"

LAYERS = [
    ("转写", "transcript.json"),
    ("规则标签", "analysis.json"),
    ("深度语义", "deep.json"),
    ("轮次切分", "rounds.json"),
    ("视觉量化", "visual.json"),
    ("话术量化", "talk.json"),
]
_PRODUCER = {"deep.json": "deep_analyze.py", "rounds.json": "rounds.py", "visual.json": "visual_metrics.py"}
LAYERS = [(k, f) for k, f in LAYERS if (ROOT / "scripts" / _PRODUCER.get(f, "analyze.py")).exists()]

SAMPLE_DIRECTIONAL = 15
SAMPLE_ASSESSABLE = 50


def rj(p, d=None):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return d


def dir_time(name):
    """场次目录名 20260922_091307 → 时间戳。"""
    try:
        return time.mktime(time.strptime(name, "%Y%m%d_%H%M%S"))
    except Exception:
        return 0


class Report:
    """收集检查项。FAIL 表示产出链条断了，WARN 表示该盯着但还没断。"""

    def __init__(self):
        self.lines = []
        self.verdict = "PASS"

    def section(self, title):
        self.lines.append("")
        self.lines.append("── %s" % title)

    def item(self, level, text):
        self.lines.append("  [%-4s] %s" % (level, text))
        if level == "FAIL":
            self.verdict = "FAIL"
        elif level == "WARN" and self.verdict == "PASS":
            self.verdict = "WARN"

    def note(self, text):
        self.lines.append("         %s" % text)


def collect_sessions():
    """所有场次：有效的（有音频）和失败的（0 字节，可能已隔离到 _failed）。"""
    ok, bad = [], []
    for sd in DATA.glob("*/*"):
        if not sd.is_dir():
            continue
        audio = sd / "audio.webm"
        size = audio.stat().st_size if audio.exists() else 0
        rec = {"rid": sd.parent.name, "name": sd.name, "t": dir_time(sd.name),
               "bytes": size, "dir": sd}
        (ok if size > 0 else bad).append(rec)
    for sd in FAILED.glob("*/*"):
        if sd.is_dir():
            bad.append({"rid": sd.parent.name, "name": sd.name,
                        "t": dir_time(sd.name), "bytes": 0, "dir": sd})
    return ok, bad


def check_output(rep, ok, bad, since, days):
    rep.section("一、采集产出（最近 %d 天）" % days)
    new_ok = [s for s in ok if s["t"] >= since]
    new_bad = [s for s in bad if s["t"] >= since]
    total = len(new_ok) + len(new_bad)
    mb = sum(s["bytes"] for s in new_ok) / 1048576

    if not new_ok:
        rep.item("FAIL", "窗口内新增有效场次 0 —— 采集没有任何产出")
    elif len(new_ok) < days:
        rep.item("WARN", "新增有效场次 %d 场（%.0fMB），平均每天不到 1 场"
                 % (len(new_ok), mb))
    else:
        rep.item("PASS", "新增有效场次 %d 场（%.0fMB）" % (len(new_ok), mb))

    if total:
        rate = len(new_bad) * 100.0 / total
        lvl = "FAIL" if rate >= 50 else ("WARN" if rate >= 20 else "PASS")
        rep.item(lvl, "采集失败率 %.0f%%（%d 失败 / %d 次尝试）"
                 % (rate, len(new_bad), total))
        if new_bad:
            from collections import Counter
            top = Counter(s["rid"] for s in new_bad).most_common(3)
            rep.note("失败最多：" + "、".join("%s×%d" % (r, n) for r, n in top))
    return new_ok


def check_layers(rep, ok):
    rep.section("二、六层覆盖（全量）")
    counts = []
    for label, fn in LAYERS:
        n = sum(1 for s in ok if (s["dir"] / fn).exists())
        counts.append((label, n))
    rep.note(" · ".join("%s %d" % (l, n) for l, n in counts))

    base = counts[0][1]
    if not base:
        rep.item("FAIL", "转写层为空，下游全部无源")
        return
    flagged = False
    for label, n in counts[1:]:
        pct = n * 100.0 / base
        if pct < 40:
            rep.item("FAIL", "%s 只覆盖 %.0f%%（%d/%d）—— 这一层大概率没接进自动链路"
                     % (label, pct, n, base))
            flagged = True
        elif pct < 70:
            rep.item("WARN", "%s 覆盖 %.0f%%（%d/%d），落后上游"
                     % (label, pct, n, base))
            flagged = True
    if not flagged:
        rep.item("PASS", "各层覆盖无明显断层")


def check_processes(rep):
    rep.section("三、进程实况")
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import install_services, cleanup_failed
    except Exception as e:
        rep.item("WARN", "进程检查不可用：%s" % str(e)[:80])
        return

    try:
        daemons = install_services.running_daemons()
    except Exception as e:
        rep.item("WARN", "守护检查失败：%s" % str(e)[:80])
        daemons = None
    if daemons is not None:
        from collections import Counter
        import os as _os
        me = _os.getpid()
        got = Counter(base for pid, base in daemons if pid != me)
        if DAEMON_MODE:
            got["health_check.py"] += 1
        for _, script, _, _ in install_services.TASKS:
            base = pathlib.Path(script).name
            n = got.get(base, 0)
            if n == 0:
                rep.item("FAIL", "守护 %s 没有在跑" % base)
            elif n > 1:
                rep.item("FAIL", "守护 %s 同时跑了 %d 份（并发会翻倍、会抢同一场次）"
                         % (base, n))
        if all(got.get(pathlib.Path(s).name, 0) == 1
               for _, s, _, _ in install_services.TASKS):
            rep.item("PASS", "%d 个守护各 1 份，均存活" % len(install_services.TASKS))

    try:
        procs = cleanup_failed.recording_procs()
        hung = []
        for pid, rid, age in procs:
            sd = cleanup_failed.session_of(rid)
            audio = (sd / "audio.webm") if sd else None
            size = audio.stat().st_size if audio and audio.exists() else 0
            if age > cleanup_failed.HUNG_MINUTES and size == 0:
                hung.append((pid, rid, age))
        if hung:
            rep.item("FAIL", "卡死录制 %d 路（占着 profile 锁，该房间会永久采不到）"
                     % len(hung))
            for pid, rid, age in hung[:5]:
                rep.note("pid %d 房间 %s 已跑 %.0f 分钟、0 字节" % (pid, rid, age))
        else:
            rep.item("PASS", "在录 %d 路，无卡死" % len(procs))

        alive = {rid for _, rid, _ in procs} - {r for _, r, _ in hung}
        orphan = sum(len(p) for rid, p in cleanup_failed.profile_browsers().items()
                     if rid not in alive)
        if orphan:
            rep.item("FAIL", "孤儿浏览器 %d 个进程占着 profile —— 跑 cleanup_failed.py --apply"
                     % orphan)
        else:
            rep.item("PASS", "无孤儿浏览器")
    except Exception as e:
        rep.item("WARN", "录制进程检查失败：%s" % str(e)[:80])


def current_run(log, banner):
    """截取当前这一轮守护的日志。
    """
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    for i in range(len(lines) - 1, -1, -1):
        if banner in lines[i]:
            return lines[i + 1:]
    return lines[-400:]


def check_silent_failures(rep):
    """专找上次那种看着正常其实全废的特征。每一条都对应一次真实事故。"""
    rep.section("四、静默故障指纹（仅本轮守护启动之后）")
    hit = False

    log = LOGS / "auto_record.log"
    tail = current_run(log, "自动采集守护启动") if log.exists() else []

    if any("never awaited" in l for l in tail):
        hit = True
        rep.item("FAIL", "本轮日志出现 'coroutine was never awaited'")
        rep.note("async playwright 漏 await 不报错，只会让判断恒为真")

    if tail:
        live = sum(1 for l in tail if "在播 ✓" in l)
        off = sum(1 for l in tail if "下播" in l)
        if live >= 10 and off == 0:
            hit = True
            rep.item("FAIL", "最近 %d 次在播判定全部为在播、0 次下播 —— 检测恒为真"
                     % live)
        elif live or off:
            rep.item("PASS", "在播判定 %d 在播 / %d 下播，分布正常" % (live, off))

        from collections import Counter
        import datetime as _dt
        starts = Counter(re.findall(r"启动录制 .*?\((\d+)\)", "\n".join(tail)))
        repeated = []
        week = (_dt.datetime.now() - _dt.timedelta(days=7)).strftime("%Y%m%d_%H%M%S")
        for r, n in starts.most_common():
            if n < 4:
                continue
            ts = []
            for p in (DATA / r).glob("*_*") if (DATA / r).exists() else []:
                try:
                    ts.append(_dt.datetime.strptime(p.name, "%Y%m%d_%H%M%S"))
                except ValueError:
                    pass
            ts.sort()
            recent = [t for t in ts if t.strftime("%Y%m%d_%H%M%S") >= week]
            dup = any((b - a).total_seconds() < 600 for a, b in zip(recent, recent[1:]))
            if dup or len(recent) < n / 2:
                repeated.append((r, n))
        if repeated:
            hit = True
            rep.item("FAIL", "同房间被反复起录：%s —— 去重或退避失效"
                     % "、".join("%s×%d" % (r, n) for r, n in repeated[:4]))

    if not hit:
        rep.item("PASS", "未发现已知的静默故障特征")


def check_samples(rep, ok):
    rep.section("五、样本充足度（标杆靶心的地基）")
    cfg = (rj(ROOMS) or {}).get("rooms", {})
    from collections import Counter
    per = Counter()
    for s in ok:
        reg = cfg.get(s["rid"], {})
        if reg.get("group") == "benchmark":
            per[reg.get("category") or "未分类"] += 1
    if not per:
        rep.item("WARN", "没有任何标杆场次")
        return
    short = []
    for cat, n in sorted(per.items(), key=lambda x: x[1]):
        if n < SAMPLE_DIRECTIONAL:
            short.append("%s %d/%d" % (cat, n, SAMPLE_DIRECTIONAL))
    rep.note(" · ".join("%s %d 场" % (c, n) for c, n in per.most_common()))
    if short:
        rep.item("WARN", "未达方向性参考门槛（%d 场）：%s"
                 % (SAMPLE_DIRECTIONAL, "、".join(short)))
        rep.note("样本不足时方法论只是少数样本的复述，不足以当靶心")
    else:
        rep.item("PASS", "各品类均达 %d 场方向性参考门槛" % SAMPLE_DIRECTIONAL)


def check_danmu(rep, new_ok):
    """弹幕采集是否真的挂上了。
    """
    rep.section("六、弹幕采集")
    cfg = (rj(ROOMS) or {}).get("rooms", {})
    per = {}
    for s in new_ok:
        m = rj(s["dir"] / "meta.json")
        if m and (m.get("duration_sec") or 0) >= 1200:
            per.setdefault(s["rid"], []).append(m.get("danmu_count") or 0)
    dead = ["%s（%d 场）" % (cfg.get(r, {}).get("name", r), len(v))
            for r, v in per.items() if len(v) >= 2 and not any(v)]
    if dead:
        rep.item("WARN", "连续多场 0 条弹幕，疑似没采到：%s" % "、".join(dead))
        rep.note("打开直播间看聊天区有没有人说话；有就是采集没挂上，不是真没弹幕")
    elif per:
        rep.item("PASS", "%d 个房间的弹幕采集都有产出" % len(per))


def build(days):
    ok, bad = collect_sessions()
    since = time.time() - days * 86400
    rep = Report()
    rep.lines.append("直播话术分析系统 · 产出体检  %s"
                     % time.strftime("%Y-%m-%d %H:%M"))
    new_ok = check_output(rep, ok, bad, since, days)
    check_layers(rep, ok)
    check_processes(rep)
    check_silent_failures(rep)
    check_samples(rep, ok)
    check_danmu(rep, new_ok)
    rep.lines.append("")
    rep.lines.append("结论：%s   （有效场次合计 %d）" % (rep.verdict, len(ok)))
    return rep, {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "verdict": rep.verdict, "days": days,
                 "sessions_total": len(ok), "sessions_new": len(new_ok),
                 "report": "\n".join(rep.lines)}


def run_once(days, quiet):
    rep, payload = build(days)
    text = "\n".join(rep.lines)
    if quiet:
        print("产出体检 %s：最近 %d 天新增 %d 场，合计 %d 场"
              % (payload["verdict"], days, payload["sessions_new"],
                 payload["sessions_total"]))
    else:
        print(text)
    try:
        (LOGS / ("health_%s.txt" % time.strftime("%Y%m%d"))).write_text(
            text, encoding="utf-8")
        (LOGS / "health_latest.txt").write_text(text, encoding="utf-8")
        (ROOT / "data" / "health.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print("报告落盘失败：%s" % str(e)[:80])
    return 0 if rep.verdict == "PASS" else (1 if rep.verdict == "WARN" else 2)


def main():
    ap = argparse.ArgumentParser(description="日更产出体检")
    ap.add_argument("--days", type=int, default=1, help="统计窗口天数，默认 1")
    ap.add_argument("--daemon", action="store_true", help="常驻，每天出一次")
    ap.add_argument("--every", type=int, default=24, help="daemon 模式间隔小时")
    ap.add_argument("--quiet", action="store_true", help="只输出结论行")
    a = ap.parse_args()

    if not a.daemon:
        return run_once(a.days, a.quiet)

    global DAEMON_MODE
    DAEMON_MODE = True

    hp = LOGS / "health.log"
    try:
        if hp.exists() and hp.stat().st_size > 1024 * 1024:
            tail = hp.read_text(encoding="utf-8", errors="replace")[-512 * 1024:]
            hp.write_text("（日志已截断，仅保留最近部分）\n" + tail,
                          encoding="utf-8")
    except Exception:
        pass
    from _proc import tee_log
    tee_log(hp)
    print("\n产出体检守护启动（每 %d 小时一次）" % a.every, flush=True)
    while True:
        try:
            run_once(a.days, False)
        except Exception as e:
            print("体检异常：%s" % str(e)[:160], flush=True)
        time.sleep(a.every * 3600)


if __name__ == "__main__":
    sys.exit(main())
