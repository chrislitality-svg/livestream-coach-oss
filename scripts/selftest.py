#!/usr/bin/env python3
"""自检：一条命令跑完开发记录第七节的验收基准，外加几条本项目特有的坑位检查。

用法:
  python scripts/selftest.py            # 全部
  python scripts/selftest.py --quick    # 跳过耗时的第 7 组
"""
import argparse, json, pathlib, re, subprocess, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
SC = ROOT / "scripts"
PY = sys.executable
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                           ("  — " + detail) if detail else ""))
    return ok


def run(args, timeout=180):
    try:
        r = subprocess.run([PY] + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=str(ROOT),
                           timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -9, "超时"
    except Exception as e:
        return -1, str(e)


def group(title):
    print("\n── %s" % title)


def t_syntax():
    group("1. 语法")
    files = sorted(SC.glob("*.py"))
    rc, out = run(["-m", "py_compile"] + [str(f) for f in files])
    check("py_compile 全部 %d 个脚本" % len(files), rc == 0, out.strip()[:120])


def t_config():
    group("2. 配置")
    need = {
        "config/rooms.json": ["rooms"],
        "config/taxonomy.json": [],
        "config/taxonomy_visual.json": ["dims"],
        "config/taxonomy_qa.json": ["buckets", "question_words"],
        "config/anchors.json": ["by_room"],
    }
    user = {"config/taxonomy_visual.json": "visual_metrics.py", "config/taxonomy_qa.json": "qa_book.py"}
    for rel, keys in need.items():
        p = ROOT / rel
        if rel in user and not (SC / user[rel]).exists():
            continue
        if not p.exists() and rel == "config/anchors.json":
            check(rel + "（可选，没有排班）", True)
            continue
        if not p.exists():
            check(rel, False, "文件不存在")
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            check(rel, False, "解析失败 %s" % str(e)[:60])
            continue
        missing = [k for k in keys if k not in d]
        check(rel, not missing, ("缺字段 " + ",".join(missing)) if missing else "")


def t_pitfalls():
    """针对这个项目交过学费的坑做静态扫描。"""
    group("3. 坑位扫描")

    bad = []
    for f in SC.glob("*.py"):
        src = f.read_text(encoding="utf-8", errors="replace")
        if "async_playwright" not in src:
            continue
        for i, line in enumerate(src.splitlines(), 1):
            s = line.strip()
            if s.startswith("#") or "await" in s:
                continue
            if re.search(r"(?<!\w)page\.(goto|evaluate|wait_for_timeout|"
                         r"screenshot|title|click|expose_binding)\s*\(", s):
                bad.append("%s:%d" % (f.name, i))
    check("playwright 调用都带 await", not bad, "、".join(bad[:5]))


    bad = []
    for f in SC.glob("*.py"):
        src = f.read_text(encoding="utf-8", errors="replace")
        if "Win32_Process" not in src:
            continue
        if "python.exe" in src and "pythonw.exe" not in src:
            bad.append(f.name)
    check("进程检测覆盖 pythonw.exe", not bad, "、".join(bad))


def t_help():
    group("4. 只读命令")
    for s in ("qa_book.py", "outcome_calib.py", "drill_plan.py",
              "visual_audit.py", "health_check.py", "cleanup_failed.py",
              "auto_record.py", "auto_one.py", "install_services.py"):
        if not (SC / s).exists():
            continue
        rc, out = run([str(SC / s), "--help"], timeout=60)
        check("%s --help" % s, rc == 0, out.strip().splitlines()[-1][:80] if rc else "")


def t_artifacts():
    group("5. 分析产物")
    layers = {"transcript.json": ["items"], "analysis.json": ["metrics", "evidence"],
              "visual.json": ["derived", "frames"], "talk.json": ["groups"]}
    base = ROOT / "data" / "douyin"
    for fn, keys in layers.items():
        found = sorted(base.glob("*/*/" + fn))
        if not found:
            check(fn, fn == "visual.json", "一个都没有" + ("（视觉层是可选的）" if fn == "visual.json" else ""))
            continue
        okn, badn = 0, []
        for p in found:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if all(k in d for k in keys):
                    okn += 1
                else:
                    badn.append(p.parent.name)
            except Exception:
                badn.append(p.parent.name)
        check("%s 可解析且字段齐（%d 份）" % (fn, len(found)), not badn,
              ("异常 %d 份" % len(badn)) if badn else "")

    sys.path.insert(0, str(SC))
    try:
        import cleanup_failed
        fresh = {rid for _, rid, age in cleanup_failed.recording_procs()
                 if age <= cleanup_failed.HUNG_MINUTES}
    except Exception:
        fresh = set()

    def is_ghost(d):
        if d.parent.name in fresh:
            return False
        a = d / "audio.webm"
        return not a.exists() or a.stat().st_size == 0
    ghosts = [d for d in base.glob("*/*") if d.is_dir() and is_ghost(d)]
    check("没有零产出的幽灵场次", not ghosts,
          ("%d 个：%s" % (len(ghosts), "、".join(g.parent.name for g in ghosts[:4])))
          if ghosts else ("在录中跳过 %d 间" % len(fresh) if fresh else ""))


def t_service():
    group("6. 本机服务")
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:8787/", timeout=8) as f:
            html = f.read(4000).decode("utf-8", "replace")
        check("8787 首页可访问", f.status == 200)
        check("首页有内容不是空壳", "<title" in html.lower() and len(html) > 500,
              "%d 字节" % len(html))
    except Exception as e:
        check("8787 首页可访问", False, str(e)[:70])
        return
    try:
        with urllib.request.urlopen("http://localhost:8787/api?view=scan",
                                    timeout=20) as f:
            d = json.loads(f.read().decode("utf-8"))
        rows = d.get("all") or d.get("sessions") or []
        if isinstance(rows, dict):
            rows = list(rows.values())
        check("scan 接口返回场次", len(rows) > 0, "%d 场" % len(rows))
    except Exception as e:
        check("scan 接口", False, str(e)[:70])


def t_features():
    group("7. 新功能端到端")
    if not all((SC / s).exists() for s in ("qa_book.py", "visual_audit.py", "outcome_calib.py", "drill_plan.py")):
        check("端到端组（本发行版不含这些模块，跳过）", True)
        return
    rc, out = run([str(SC / "qa_book.py")], timeout=300)
    made = re.search(r"生成 (\d+) 份手册", out)
    check("消费者问题手册", rc == 0 and made and int(made.group(1)) > 0,
          (made.group(0) if made else out.strip()[-70:]))

    rc, out = run([str(SC / "visual_audit.py"), "--sample", "5"], timeout=120)
    check("视觉抽检生成复核页", rc == 0 and (ROOT / "data" / "audit" /
                                            "visual_audit.html").exists(),
          out.strip().splitlines()[0][:70] if out else "")

    rc, out = run([str(SC / "outcome_calib.py")], timeout=120)
    has_outcomes = (ROOT / "data" / "outcomes.json").exists()
    if has_outcomes:
        check("实绩校准", rc == 0, out.strip().splitlines()[-1][:70])
    else:
        check("实绩校准（无数据时明确报缺）",
              rc == 1 and "outcomes.json" in out, out.strip().splitlines()[0][:70])

    cfg = json.loads((ROOT / "config" / "rooms.json").read_text(encoding="utf-8"))
    room = None
    for rid, v in cfg["rooms"].items():
        if v.get("group") != "own":
            continue
        if any((ROOT / "data" / "douyin" / rid).glob("*/analysis.json")):
            room = rid
            break
    if not room:
        check("训练卡", False, "没有已分析的自家房间可测")
    else:
        rc, out = run([str(SC / "drill_plan.py"), "--room", room], timeout=120)
        check("训练卡", rc == 0 and "训练卡 →" in out,
              out.strip().splitlines()[0][:70] if out else "")


def t_daemons():
    group("8. 守护进程")
    sys.path.insert(0, str(SC))
    try:
        import install_services
        got = {}
        for _, base in install_services.running_daemons():
            got[base] = got.get(base, 0) + 1
        for _, script, _, _ in install_services.TASKS:
            b = pathlib.Path(script).name
            n = got.get(b, 0)
            check("守护 %s" % b, n == 1,
                  "没在跑" if n == 0 else ("跑了 %d 份" % n))
    except Exception as e:
        check("守护检查", False, str(e)[:70])


def main():
    ap = argparse.ArgumentParser(description="项目自检")
    ap.add_argument("--quick", action="store_true", help="跳过耗时的端到端组")
    a = ap.parse_args()
    print("直播话术分析系统 · 自检  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    t_syntax(); t_config(); t_pitfalls(); t_help()
    t_artifacts(); t_service()
    if not a.quick:
        t_features()
    t_daemons()

    bad = [n for n, ok, _ in results if not ok]
    print("\n%d 项检查，%d 项未通过" % (len(results), len(bad)))
    if bad:
        for n in bad:
            print("  ✗ %s" % n)
    print("\n结论：%s" % ("PASS" if not bad else "FAIL"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
