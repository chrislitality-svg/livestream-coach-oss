#!/usr/bin/env python3
"""把分析平台与自动分析守护设为开机自启。

用法:
  python scripts/install_services.py            # 安装并立即启动
  python scripts/install_services.py --status
  python scripts/install_services.py --restart  # 改完守护代码后重启才生效
  python scripts/install_services.py --stop
  python scripts/install_services.py --uninstall
"""
import argparse, os, pathlib, subprocess, sys, time
try:
    CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
except Exception:
    CREATE_NO_WINDOW = 0


try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYW = pathlib.Path(sys.executable).with_name("pythonw.exe")
if not PYW.exists():
    PYW = pathlib.Path(sys.executable)

STARTUP = (pathlib.Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows"
           / "Start Menu" / "Programs" / "Startup")

TASKS = [
    ("LivestreamCoach-Dashboard", "scripts/dashboard.py", ["--port", "8787"], "0000:30"),
    ("LivestreamCoach-AutoAnalyze", "scripts/auto_analyze.py",
     ["--every", "120", "--concurrency", "3"], "0001:00"),
    ("LivestreamCoach-AutoRecord", "scripts/auto_record.py", ["--every", "30"], "0002:00"),
    ("LivestreamCoach-HealthCheck", "scripts/health_check.py",
     ["--daemon", "--every", "24"], "0003:00"),
    ("LivestreamCoach-RequestWorker", "scripts/request_worker.py",
     ["--every", "3"], "0004:00"),
]
TASKS = [t for t in TASKS if (ROOT / t[1]).exists()]

DETACHED = 0x00000008


def run(args):
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                     creationflags=CREATE_NO_WINDOW)


def vbs_body(script, extra):
    """VBS 字符串里的双引号要写成两个。用 VBS 而不是 bat：bat 会闪黑窗。"""
    inner = '""%s"" ""%s""' % (PYW, ROOT / script)
    if extra:
        inner += " " + " ".join(extra)
    lines = [
        'Set ws = CreateObject("WScript.Shell")',
        'ws.CurrentDirectory = "%s"' % ROOT,
        'ws.Run "%s", 0, False' % inner,
    ]
    return "\r\n".join(lines) + "\r\n"


def install_startup():
    if not STARTUP.exists():
        print("  找不到启动文件夹 %s" % STARTUP)
        return False
    for name, script, extra, _ in TASKS:
        (STARTUP / (name + ".vbs")).write_text(vbs_body(script, extra), encoding="utf-8")
        print("  [OK] %s -> 启动文件夹" % name)
    return True


def running_daemons():
    """在跑的守护进程：[(pid, 脚本名)]。只认本项目的三个守护，
    不碰它们起的 record.py 子进程 —— 重启守护不该打断正在进行的录制。
    """
    names = "|".join(pathlib.Path(s).name.replace(".", "\\.")
                     for _, s, _, _ in TASKS)
    r = run(["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process "
             "-Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
             "Where-Object { $_.CommandLine -match '(%s)' } | "
             "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }" % names])
    out = []
    for ln in (r.stdout or "").splitlines():
        pid, _, cmd = ln.strip().partition("|")
        if not pid.isdigit():
            continue
        for _, script, _, _ in TASKS:
            base = pathlib.Path(script).name
            if base in cmd:
                out.append((int(pid), base))
                break
    return out


def stop():
    """停掉在跑的守护。
    """
    procs = running_daemons()
    if not procs:
        print("  没有在跑的守护进程")
        return 0
    args = ["taskkill", "/F"]
    for pid, base in procs:
        print("  停止 %s (pid %d)" % (base, pid))
        args += ["/PID", str(pid)]
    run(args)
    time.sleep(2)
    return len(procs)


def spawn_detached(args):
    """用 WMI 建进程：新进程的父进程是 WmiPrvSE，彻底脱离调用方的进程树。
    """
    q = lambda x: "'" + str(x).replace("'", "''") + "'"
    cmd = subprocess.list2cmdline([str(a) for a in args])
    ps = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
          "-Arguments @{CommandLine=%s; CurrentDirectory=%s}; exit $r.ReturnValue"
          % (q(cmd), q(ROOT)))
    if run(["powershell", "-NoProfile", "-Command", ps]).returncode == 0:
        return True
    subprocess.Popen(args, cwd=str(ROOT), creationflags=DETACHED)
    return False


def launch_now():
    stop()
    for name, script, extra, _ in TASKS:
        if run(["schtasks", "/Run", "/TN", name]).returncode != 0:
            spawn_detached([str(PYW), str(ROOT / script)] + list(extra))


def install():
    print("项目目录 %s" % ROOT)
    print("解释器   %s\n" % PYW)
    fail = False
    for name, script, extra, delay in TASKS:
        cmd = '"%s" "%s"' % (PYW, ROOT / script)
        if extra:
            cmd += " " + " ".join(extra)
        r = run(["schtasks", "/Create", "/TN", name, "/F", "/TR", cmd,
                 "/SC", "ONLOGON", "/DELAY", delay])
        if r.returncode == 0:
            print("[OK] 计划任务 %s" % name)
        else:
            print("[跳过] 计划任务 %s：%s" % (name, (r.stderr or r.stdout).strip()[:60]))
            fail = True
    if fail:
        print("\n计划任务被限制（多为组策略），改用启动文件夹：")
        install_startup()

    print("\n立即启动 ...")
    launch_now()
    time.sleep(6)
    print()
    status()
    print("\n平台地址 http://localhost:8787")


def status():
    import urllib.request
    for name, _, _, _ in TASKS:
        r = run(["schtasks", "/Query", "/TN", name, "/FO", "LIST"])
        if r.returncode == 0:
            st = ""
            for ln in (r.stdout or "").splitlines():
                if ":" in ln and ("状态" in ln or "Status" in ln):
                    st = ln.split(":", 1)[1].strip()
            print("[计划任务] %-30s %s" % (name, st))
        else:
            f = STARTUP / (name + ".vbs")
            print("[%s] %s" % ("启动文件夹" if f.exists() else "未安装  ", name))
    try:
        with urllib.request.urlopen("http://localhost:8787/", timeout=4) as f:
            print("[在线]     分析平台 HTTP %d" % f.status)
    except Exception as e:
        print("[离线]     分析平台：%s" % str(e)[:60])


def uninstall():
    for name, _, _, _ in TASKS:
        run(["schtasks", "/Delete", "/TN", name, "/F"])
        f = STARTUP / (name + ".vbs")
        if f.exists():
            f.unlink()
        print("[OK] 已移除 %s" % name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="设为开机自启")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--stop", action="store_true", help="停掉在跑的守护")
    ap.add_argument("--restart", action="store_true",
                    help="停掉再拉起（改完守护代码后必须做，否则跑的还是旧代码）")
    a = ap.parse_args()
    if a.uninstall:
        uninstall()
    elif a.status:
        status()
    elif a.stop:
        stop()
    elif a.restart:
        launch_now()
        time.sleep(6)
        status()
    else:
        install()
