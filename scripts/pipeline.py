#!/usr/bin/env python3
"""一条命令跑完整条链路：录制 -> 转写 -> 分析 -> 报告
"""
import argparse, json, pathlib, subprocess, sys, time
try:
    CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
except Exception:
    CREATE_NO_WINDOW = 0


try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
SC = ROOT / "scripts"
DATA = ROOT / "data"
PY = sys.executable


def step(title, args):
    print("\n" + "=" * 58)
    print("  " + title)
    print("=" * 58, flush=True)
    r = subprocess.run([PY, str(SC / args[0])] + args[1:],
                     creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise SystemExit("步骤失败：%s（退出码 %d）" % (title, r.returncode))


def newest_under(base):
    dirs = [p for p in base.rglob("meta.json")]
    if not dirs:
        raise SystemExit("找不到任何录制目录")
    return max(dirs, key=lambda p: p.stat().st_mtime).parent


def main():
    ap = argparse.ArgumentParser(description="采集与分析全流程")
    ap.add_argument("platform", nargs="?", default="douyin")
    ap.add_argument("url", nargs="?")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--shot-every", type=int, default=15)
    ap.add_argument("--model", default="large-v3", help="本地转写模型")
    ap.add_argument("--engine", default="local", choices=["local", "nano", "aliyun"])
    ap.add_argument("--prompt", default="", help="场景提示词：品牌名+品类，显著提升专有名词准确率")
    ap.add_argument("--redo", help="跳过录制，直接处理这个已有录制目录")
    ap.add_argument("--out", help="采集数据根目录")
    a = ap.parse_args()

    t0 = time.time()
    if a.redo:
        recdir = pathlib.Path(a.redo).expanduser()
        if not recdir.is_dir():
            raise SystemExit("--redo 指定的不是一个目录：%s" % recdir)
        if not (recdir / "audio.webm").exists():
            raise SystemExit("这个目录里没有 audio.webm，不像是录制目录：%s" % recdir)
    else:
        if not a.url:
            raise SystemExit("需要直播间地址，或用 --redo 指定已有录制目录")
        base = pathlib.Path(a.out).expanduser() if a.out else DATA
        before = {p.parent for p in base.rglob("meta.json")} if base.exists() else set()
        args = ["record.py", a.platform, a.url, "--minutes", str(a.minutes),
                "--shot-every", str(a.shot_every)]
        if a.out:
            args += ["--out", a.out]
        step("1/4  采集（音频 + 弹幕 + 画面）", args)
        after = {p.parent for p in base.rglob("meta.json")}
        new = after - before
        recdir = new.pop() if new else newest_under(base)

    step("2/4  语音转写", ["transcribe.py", str(recdir),
                       "--engine", a.engine, "--model", a.model, "--prompt", a.prompt])
    step("3/4  话术分析", ["analyze.py", str(recdir)])
    step("4/4  生成报告", ["report.py", str(recdir)])

    print("\n" + "=" * 58)
    print("  全部完成（总耗时 %.0f 分钟）" % ((time.time() - t0) / 60))
    print("=" * 58)
    try:
        meta = json.loads((recdir / "meta.json").read_text(encoding="utf-8"))
        an = json.loads((recdir / "analysis.json").read_text(encoding="utf-8"))
        m = an.get("metrics") or {}
        print("  直播间 %s  时长 %ss  弹幕 %s 条  截图 %s 张"
              % (meta.get("room_id", "?"), meta.get("duration_sec", "?"),
                 meta.get("danmu_count", "?"), meta.get("frames", "?")))
        print("  产品力 %s   销售力 %s   互动力 %s"
              % (m.get("产品力_标签覆盖", "—"), m.get("销售力_标签覆盖", "—"),
                 m.get("互动力_标签覆盖", "—")))
        risk = (m.get("口播风险话术") or 0) + (m.get("弹幕风险话术") or 0)
        if risk:
            print("  !! 违规风险话术命中 %d 处，见报告" % risk)
    except Exception as e:
        print("  （摘要读取失败，不影响已产出的结果：%s）" % str(e)[:80])
    print("\n  报告 %s" % (recdir / "report.html"))


if __name__ == "__main__":
    main()
