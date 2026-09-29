#!/usr/bin/env python3
"""单场自动处理：六层流水线跑完一场。被 auto_analyze.py 并发调用。

用法:  python scripts/auto_one.py <录制目录>
       python scripts/auto_one.py <录制目录> --llm-only   # 只补缺的模型层（auto_analyze 补跑用）
"""
import json, pathlib, subprocess, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _config
import _llm

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
SC = ROOT / "scripts"
ROOMS = ROOT / "config" / "rooms.json"
PY = sys.executable

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

CAT_PROMPT = {
    "手机数码": "手机直播带货，处理器，内存，闪存，续航，快充，影像，屏幕，以旧换新，1号链接，运费险",
    "厨房小家电": "厨房小家电直播带货，内胆，容量，功率，不粘，1号链接，运费险，赠品",
    "家电": "家电直播带货，能效，容量，静音，质保，1号链接，运费险",
    "大家电-冰箱": "冰箱直播带货，容量，能效，变频，保鲜，制冷，以旧换新，1号链接，运费险",
    "大家电-彩电": "电视直播带货，分区，亮度，刷新率，量子点，MiniLED，以旧换新，1号链接",
    "大家电-烟灶": "烟灶直播带货，风量，风压，静音，火力，燃热，以旧换新，1号链接",
}
DEFAULT_PROMPT = "直播带货，家人们，1号链接，运费险，赠品，限时"


def rooms_cfg():
    try:
        return json.loads(ROOMS.read_text(encoding="utf-8")).get("rooms", {})
    except Exception:
        return {}


def step(label, args, d):
    """跑流水线的一步，失败时把完整输出落盘，返回是否成功。
    """
    if not pathlib.Path(args[0]).exists():
        return True
    r = subprocess.run([PY] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       creationflags=CREATE_NO_WINDOW)
    if r.returncode == 0:
        return True
    logf = ROOT / "logs" / ("auto_one_%s_%s.log" % (d.parent.name, d.name))
    try:
        logf.parent.mkdir(parents=True, exist_ok=True)
        with open(logf, "a", encoding="utf-8") as f:
            f.write("\n===== %s  %s  退出码 %d =====\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), label, r.returncode))
            f.write("--- stdout ---\n%s\n--- stderr ---\n%s\n"
                    % (r.stdout or "", r.stderr or ""))
    except Exception:
        pass
    lines = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
    print("  [%s] 失败（退出码 %d）：%s  详见 logs/%s"
          % (label, r.returncode, lines[-1][:110] if lines else "无输出", logf.name),
          flush=True)
    return False


def main(d):
    d = pathlib.Path(d)
    rid = d.parent.name
    cfg = rooms_cfg()
    cat = cfg.get(rid, {}).get("category", "")
    prompt = CAT_PROMPT.get(cat, DEFAULT_PROMPT)
    name = cfg.get(rid, {}).get("name", rid)
    print("[%s] 开始分析 %s  %s" % (time.strftime("%H:%M:%S"), name, d.name), flush=True)
    if not step("转写+标签", [str(SC / "pipeline.py"), "--redo", str(d),
                              "--engine", _config.asr_engine(), "--prompt", prompt], d):
        return 1
    print("  规则层完成，继续深度分析 ...", flush=True)
    step("深度语义", [str(SC / "deep_analyze.py"), str(d)], d)
    step("轮次切分", [str(SC / "rounds.py"), str(d)], d)
    if not (d / "visual.json").exists() and _llm.vl_model():
        step("视觉量化", [str(SC / "visual_metrics.py"), str(d)], d)
    step("话术量化", [str(SC / "talk_metrics.py"), str(d)], d)
    step("成交与报价", [str(SC / "commerce.py"), str(d)], d)
    try:
        an = json.loads((d / "analysis.json").read_text(encoding="utf-8"))
        m = an["metrics"]
        print("  完成 %s：产品力 %s 销售力 %s 互动力 %s 风险 %d" % (
            name, m.get("产品力_标签覆盖"), m.get("销售力_标签覆盖"),
            m.get("互动力_标签覆盖"),
            (m.get("口播风险话术", 0) or 0) + (m.get("弹幕风险话术", 0) or 0)), flush=True)
    except Exception:
        print("  完成", flush=True)
    if cat:
        step("方法论重算", [str(SC / "method.py"), cat], d)
    return 0


def llm_only(d):
    """只补这一场缺的模型层（深度语义 / 轮次切分 / 视觉量化），补完重出报告。
    """
    d = pathlib.Path(d)
    todo = [(f, label, args) for f, label, args in (
        ("deep.json", "深度语义", [str(SC / "deep_analyze.py"), str(d)]),
        ("rounds.json", "轮次切分", [str(SC / "rounds.py"), str(d)]),
        ("visual.json", "视觉量化", [str(SC / "visual_metrics.py"), str(d)]),
    ) if not (d / f).exists() and (f != "visual.json" or _llm.vl_model())]
    if not todo:
        return 0
    print("[%s] 补模型层 %s/%s：%s" % (time.strftime("%H:%M:%S"), d.parent.name, d.name,
                                     "、".join(t[1] for t in todo)), flush=True)
    ok = all([step(label, args, d) for _, label, args in todo])
    step("报告", [str(SC / "report.py"), str(d)], d)
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    only = "--llm-only" in args
    args = [x for x in args if x != "--llm-only"]
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        print("\n示例:  python scripts/auto_one.py data/douyin/<房间号>/<时间戳>")
        sys.exit(0 if args and args[0] in ("-h", "--help") else 2)
    d = pathlib.Path(args[0])
    if not (d / "audio.webm").exists():
        print("不像是录制目录（缺 audio.webm）：%s" % d)
        sys.exit(2)
    sys.exit(llm_only(d) if only else main(d))
