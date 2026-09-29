#!/usr/bin/env python3
"""直播话术分析平台：标杆库浏览、单场诊断、我方对标、实时监控。

用法:
  python scripts/dashboard.py           # http://localhost:8787
"""
import argparse, json, pathlib, re, statistics, sys, time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TAXO = ROOT / "config" / "taxonomy.json"
ROOMS = ROOT / "config" / "rooms.json"

SPAM_MIN = 3
SPAM_MINLEN = 6

CORE = [
    ("语速_字每分", "语速", "字/分", None),
    ("行动指令_次每分", "行动指令密度", "次/分", True),
    ("互动发起_次每分", "互动发起密度", "次/分", True),
    ("最长无逼单间隔_秒", "最长无逼单间隔", "秒", False),
    ("弹幕_条每分", "弹幕密度", "条/分", True),
    ("购买意向弹幕", "购买意向弹幕", "条", True),
]


def rj(p, d=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return d


def rjl(p, tail=None):
    if not p.exists():
        return []
    ls = p.read_text(encoding="utf-8").splitlines()
    if tail:
        ls = ls[-tail:]
    out = []
    for x in ls:
        try:
            out.append(json.loads(x))
        except Exception:
            pass
    return out


def taxo():
    return rj(TAXO)


def rooms_cfg():
    return (rj(ROOMS) or {}).get("rooms", {})


_SCAN_MEMO = {}


def _sig(d, a, cfg_sig):
    """一条场次的廉价新鲜度指纹：只 stat，不读内容。
    """
    def st(x):
        try:
            r = x.stat()
            return (r.st_mtime_ns, r.st_size)
        except OSError:
            return None
    return (cfg_sig, st(a), st(d / "meta.json"), st(d / "analysis.json"),
            st(d / "danmu.jsonl"), st(d / "live.jsonl"),
            st(d / "transcript.json"), st(d / "frames"), st(d / "report.html"))


def scan():
    """扫出所有场次，附带登记信息与分析结果。
    """
    cfg = rooms_cfg()
    try:
        cs = ROOMS.stat()
        cfg_sig = (cs.st_mtime_ns, cs.st_size)
    except OSError:
        cfg_sig = None
    out = []
    if not DATA.exists():
        return out
    seen = set()
    for d in sorted(DATA.glob("*/*/*"), key=lambda p: p.name, reverse=True):
        a = d / "audio.webm"
        if not d.is_dir() or not a.exists():
            continue
        seen.add(d)
        sig = _sig(d, a, cfg_sig)
        hit = _SCAN_MEMO.get(d)
        if hit is not None and hit[0] == sig:
            out.append(dict(hit[1]))
            continue
        if a.stat().st_size == 0 and not any((d / "frames").glob("*.jpg")):
            continue
        rid = d.parent.name
        info = rj(d / "info.json", {}) or {}
        meta = rj(d / "meta.json")
        reg = cfg.get(rid, {})
        frames = sorted((d / "frames").glob("*.jpg")) if (d / "frames").exists() else []
        dmp = d / "danmu.jsonl"
        n_dm = sum(1 for _ in dmp.open(encoding="utf-8")) if dmp.exists() else 0
        first = frames[0].stat().st_mtime if frames else None
        elapsed = meta["duration_sec"] if meta else (
            max(a.stat().st_mtime - first + 10, 0) if first else 0)
        live = rjl(d / "live.jsonl")
        tr = rj(d / "transcript.json")
        an = rj(d / "analysis.json")
        out.append({
            "path": str(d.relative_to(ROOT)).replace("\\", "/"),
            "room": rid,
            "name": reg.get("name") or info.get("title") or rid,
            "group": reg.get("group", "unknown"),
            "category": reg.get("category", "未分类"),
            "started": d.name, "done": meta is not None,
            "elapsed": elapsed, "danmu": n_dm, "frames": len(frames),
            "chars": tr["chars"] if tr else sum(len(s["text"]) for s in live),
            "analyzed": an is not None,
            "has_report": (d / "report.html").exists(),
            "last_say": live[-1]["text"][:46] if live else "",
            "mtime": a.stat().st_mtime, "dir": d,
        })
        _SCAN_MEMO[d] = (sig, dict(out[-1]))
    for k in [k for k in _SCAN_MEMO if k not in seen]:
        _SCAN_MEMO.pop(k, None)
    return out


def dim_cover(an):
    dims = taxo()["dims"]
    tc = an.get("tag_count", {})
    return [{"dim": dk, "name": dv["name"],
             "used": sum(1 for tk in dv["tags"] if tc.get(dk + "." + tk)),
             "total": len(dv["tags"])} for dk, dv in dims.items()]


def split_danmu(rows):
    cnt = Counter(r.get("text", "") for r in rows)
    real, spam = [], []
    for r in rows:
        t = r.get("text", "")
        if not r.get("nick") and (t.endswith("来了") or t.startswith("欢迎来到直播间")):
            continue
        host = r.get("role") == "anchor"
        (spam if host or (cnt[t] >= SPAM_MIN and len(t) >= SPAM_MINLEN) else real).append(r)
    agg = [{"text": t, "n": n} for t, n in Counter(x.get("text", "") for x in spam).most_common(8)]
    return real[-25:], agg, len(spam)


def session_detail(s):
    d = s["dir"]
    an = rj(d / "analysis.json")
    tr = rj(d / "transcript.json")
    live = rjl(d / "live.jsonl", tail=200)
    dims = taxo()["dims"]
    name_of = {dk + "." + tk: tv["name"] for dk, dv in dims.items() for tk, tv in dv["tags"].items()}

    flow = []
    if an:
        for seg in an.get("segments", []):
            flow.append({"start": seg["start"], "text": seg.get("text_p") or seg["text"],
                         "tags": [{"dim": t["dim"], "name": t["name"]} for t in seg["tags"]],
                         "danmu_after": seg.get("danmu_after", 0),
                         "buy_after": seg.get("danmu_buy_after", 0)})
    elif live:
        flow = [{"start": x["start"], "text": x.get("punct") or x["text"],
                 "tags": [{"dim": t["dim"], "name": t["name"]} for t in x.get("tags", [])],
                 "danmu_after": 0, "buy_after": 0} for x in live]

    rows = rjl(d / "danmu.jsonl", tail=300)
    real, spam, n_spam = split_danmu(rows)

    tc = (an or {}).get("tag_count", {})
    risk = []
    for k, v in tc.items():
        if k.startswith("risk."):
            ev = ((an or {}).get("evidence") or {}).get(k, [{}])[0]
            risk.append({"name": name_of.get(k, k), "t": ev.get("t", 0),
                         "text": ev.get("text", "")[:80], "src": "口播"})
    for r in (an or {}).get("danmu", []):
        if r.get("risk") and len(risk) < 6:
            risk.append({"name": name_of.get("risk." + r["risk"][0], ""),
                         "t": r.get("rel", 0), "text": r.get("text", "")[:80], "src": "弹幕"})

    return {
        **{k: v for k, v in s.items() if k != "dir"},
        "metrics": (an or {}).get("metrics", {}),
        "dims": dim_cover(an) if an else [],
        "top": [{"name": name_of.get(k, k), "n": n}
                for k, n in sorted(tc.items(), key=lambda x: -x[1])[:10]],
        "flow": flow[-120:], "danmu_real": real, "danmu_spam": spam, "spam_total": n_spam,
        "risk": risk,
        "transcript_chars": tr["chars"] if tr else 0,
        "deep": rj(d / "deep.json"),
        "visual_metrics": _vm_with_examples(d),
        "on_duty": session_anchors(s["room"], (rj(d / "info.json") or {}).get("started_at", "")),
        "talk": rj(d / "talk.json"),
        "rounds": (rj(d / "rounds.json") or {}).get("rounds", []),
        "round_analysis": round_analysis(d),
    }


def benchmark(category, exclude_group="own"):
    """同品类标杆聚合。样本不足时照实说，不硬给分位数。"""
    ss = [s for s in scan()
          if s["analyzed"] and s["category"] == category and s["group"] != exclude_group]
    dims = taxo()["dims"]
    name_of = {dk + "." + tk: tv["name"] for dk, dv in dims.items() for tk, tv in dv["tags"].items()}
    ans = [rj(s["dir"] / "analysis.json") for s in ss]
    ans = [a for a in ans if a]
    if not ans:
        return {"category": category, "n": 0, "sessions": [], "metrics": [], "tags": []}

    mets = []
    for key, label, unit, hi in CORE:
        vals = [a["metrics"].get(key) for a in ans if isinstance(a["metrics"].get(key), (int, float))]
        if not vals:
            continue
        vals.sort()
        mets.append({"key": key, "name": label, "unit": unit, "n": len(vals),
                     "min": round(min(vals), 1), "max": round(max(vals), 1),
                     "median": round(statistics.median(vals), 1),
                     "p75": round(vals[int(len(vals) * 0.75)] if len(vals) > 1 else vals[0], 1)})
    tagc = Counter()
    seen_in = Counter()
    for a in ans:
        for k, n in a.get("tag_count", {}).items():
            tagc[k] += n
            seen_in[k] += 1
    tags = [{"key": k, "name": name_of.get(k, k), "total": n,
             "rooms": seen_in[k], "rate": round(seen_in[k] / len(ans) * 100)}
            for k, n in tagc.most_common(16)]
    return {
        "category": category, "n": len(ans),
        "sessions": [{"name": s["name"], "started": s["started"], "path": s["path"],
                      "elapsed": s["elapsed"], "chars": s["chars"]} for s in ss],
        "metrics": mets, "tags": tags,
        "reliable": len(ans) >= 15,
    }


def compare(a_path, b_path):
    """a=待评估场次，b=对标场次。输出差异与可借鉴例句。"""
    ss = {s["path"]: s for s in scan()}
    sa, sb = ss.get(a_path), ss.get(b_path)
    if not sa or not sb:
        return {"error": "场次不存在"}
    an_a, an_b = rj(sa["dir"] / "analysis.json"), rj(sb["dir"] / "analysis.json")
    if not an_a or not an_b:
        return {"error": "有场次尚未完成分析"}
    dims = taxo()["dims"]
    name_of = {dk + "." + tk: tv["name"] for dk, dv in dims.items() for tk, tv in dv["tags"].items()}
    ta, tb = an_a.get("tag_count", {}), an_b.get("tag_count", {})

    dim_rows = []
    for dk, dv in dims.items():
        ua = sum(1 for tk in dv["tags"] if ta.get(dk + "." + tk))
        ub = sum(1 for tk in dv["tags"] if tb.get(dk + "." + tk))
        dim_rows.append({"dim": dk, "name": dv["name"], "total": len(dv["tags"]),
                         "a": ua, "b": ub, "gap": ua - ub})

    missing = []
    for k, n in sorted(tb.items(), key=lambda x: -x[1]):
        if k.startswith("risk.") or ta.get(k):
            continue
        ev = (an_b.get("evidence") or {}).get(k, [])
        missing.append({"key": k, "name": name_of.get(k, k), "dim": k.split(".")[0],
                        "bench_n": n,
                        "examples": [{"t": e["t"], "text": e["text"],
                                      "hit": "、".join(e.get("hit", [])[:3])} for e in ev[:2]]})

    extra = [{"name": name_of.get(k, k), "n": n} for k, n in ta.items()
             if not tb.get(k) and not k.startswith("risk.")]

    met_rows = []
    for key, label, unit, hi in CORE:
        va, vb = an_a["metrics"].get(key), an_b["metrics"].get(key)
        if not isinstance(va, (int, float)) or not isinstance(vb, (int, float)):
            continue
        diff = va - vb
        pct = round(diff / vb * 100) if vb else 0
        good = None if hi is None else (diff >= 0 if hi else diff <= 0)
        met_rows.append({"name": label, "unit": unit, "a": va, "b": vb,
                         "diff": round(diff, 1), "pct": pct, "good": good})

    return {
        "a": {"name": sa["name"], "started": sa["started"], "path": sa["path"],
              "group": sa["group"], "elapsed": sa["elapsed"]},
        "b": {"name": sb["name"], "started": sb["started"], "path": sb["path"],
              "group": sb["group"], "elapsed": sb["elapsed"]},
        "dims": dim_rows, "missing": missing[:12], "extra": extra[:8], "metrics": met_rows,
    }


PAGE_FILE = ROOT / "scripts" / "dashboard.html"
METHOD_DIR = ROOT / "data" / "method"


def round_analysis(d):
    """读该场已完成的单轮诊断（round_NN.json + 人工时段的 round_custom_*.json）。"""
    out = []
    for f in sorted(pathlib.Path(d).glob("round_*.json")):
        x = rj(f)
        if not x or "_meta" not in x:
            continue
        x["idx"] = x["_meta"]["idx"]
        out.append(x)
    return out


def talk_profile(category=None, group=None):
    """话术画像：三类话术占比（塑品/互动/逼单）+ 促单空窗。
    不传 category 时给全场总榜（每场一行，按逼单占比排序）——这是
    "谁在逼单、谁在陪聊"最直观的一张表。
    """
    rows = []
    for s in scan():
        t = rj(s["dir"] / "talk.json")
        if not t or not t.get("groups"):
            continue
        if category and s["category"] != category:
            continue
        if group and s["group"] != group:
            continue
        g = t["groups"]
        rows.append({
            "path": s["path"], "name": s["name"], "room": s["room"],
            "group": s["group"], "category": s["category"], "started": s["started"],
            "total_min": round(t.get("total_sec", 0) / 60, 1),
            "product_pct": g["塑品"]["pct"], "interact_pct": g["互动"]["pct"],
            "push_pct": g["逼单"]["pct"], "other_pct": t.get("other", {}).get("pct", 0),
            "push_per_min": g["逼单"].get("per_min", 0),
            "push_span_avg": g["逼单"].get("avg_span_sec", 0),
            "product_span_avg": g["塑品"].get("avg_span_sec", 0),
            "interact_span_avg": g["互动"].get("avg_span_sec", 0),
            "coverage": g["逼单"].get("coverage_decile", 0),
            "push_gap_max": (t.get("push_gap") or {}).get("max_sec", 0),
            "push_top": (g["逼单"].get("top") or [{}])[0].get("name", ""),
            "product_top": (g["塑品"].get("top") or [{}])[0].get("name", ""),
            "interact_top": (g["互动"].get("top") or [{}])[0].get("name", ""),
        })
    avg = None
    if category and rows:
        n = len(rows)
        avg = {k: round(sum(r[k] for r in rows) / n, 1)
               for k in ("product_pct", "interact_pct", "push_pct", "push_per_min")}
    return {"category": category, "n": len(rows), "rows": rows,
            "avg": avg}


FILLERS = ["嗯", "啊", "呃", "那个", "这个", "就是说", "对吧", "是不是", "然后呢",
           "咱就是说", "说一下啊", "哈"]
CATEGORY_TERMS = {
    "大家电-冰箱": ["变频", "能效", "压缩机", "保鲜", "风冷", "直冷", "双循环", "双系统",
                 "嵌入", "零嵌", "超薄", "容量", "温控", "除菌", "净味", "制冰", "冷藏",
                 "冷冻", "干湿分储", "全空间", "科技", "智能"],
    "大家电-彩电": ["刷新率", "背光", "分区", "量子点", "MiniLED", "QLED", "HDR", "色域",
                 "分辨率", "MEMC", "芯片", "尼特", "峰值亮度", "HDMI", "全面屏", "护眼",
                 "4K", "85寸", "75寸", "65寸"],
    "大家电-烟灶": ["风量", "风压", "火力", "热效率", "自清洗", "燃气", "吸力", "静音",
                 "烟机", "灶具", "息屏", "巡航", "挥手", "智控", "猛火", "一级能效"],
    "手机数码": ["处理器", "快充", "续航", "像素", "刷新率", "运存", "闪存", "影像",
               "芯片", "5G", "电池容量", "分辨率", "折叠", "铰链", "防水", "散热",
               "影像", "光变", "防抖", "NFC"],
    "厨房小家电": ["内胆", "容量", "功率", "不粘", "涂层", "预约", "保温", "蒸汽",
                "空气炸", "微压", "炖", "蒸", "搅拌", "破壁", "转速", "钛"],
    "大家电": ["能效", "变频", "容量", "一级能效", "国标", "质保", "变频", "静音"],
}
TERMS_FALLBACK = ["质保", "联保", "国标", "认证", "检测", "技术", "系统", "配置",
                  "专利", "实验室"]


def lang_metrics(transcript_path, category):
    """口语层量化：填充词率（越低越流畅）与品类术语密度（越高越专业）。
    """
    tr = rj(transcript_path)
    if not tr or not tr.get("items"):
        return None
    text = "".join(x.get("text", "") for x in tr["items"])
    n = len(text)
    if n < 200:
        return None
    k = n / 1000.0
    filler = sum(text.count(w) for w in FILLERS)
    terms = CATEGORY_TERMS.get(category) or (CATEGORY_TERMS.get(
        next((c for c in CATEGORY_TERMS if c and c in category), ""), TERMS_FALLBACK))
    hits = Counter()
    for t in terms:
        c = text.count(t)
        if c:
            hits[t] = c
    return {
        "filler_rate": round(filler / k, 1),
        "term_rate": round(sum(hits.values()) / k, 1),
        "term_top": [{"term": t, "n": c} for t, c in hits.most_common(6)],
        "chars": n,
    }


def _score_rel(ours, bench, hi=True):
    """相对标杆打分：以标杆均值为锚，1-10。达到标杆=8，超 15%=9.5，超 30%=10。"""
    if ours is None or not bench:
        return None
    ratio = (ours / bench) if hi and bench else ((bench / ours) if ours else 99)
    table = [(0.35, 2.0), (0.5, 3.5), (0.7, 5.0), (0.85, 6.5), (1.0, 8.0),
             (1.15, 9.5), (1.30, 10.0)]
    for th, sc in table:
        if ratio <= th:
            return sc
    return 10.0


def _score_dev(ours, bench, tol=0.35):
    """贴近型打分：与标杆偏离越小分越高（话术结构占比这类）。"""
    if ours is None or not bench:
        return None
    dev = abs(ours - bench) / abs(bench)
    if dev <= 0.08:
        return 9.5
    if dev <= 0.2:
        return 8.0
    if dev <= tol:
        return 6.5
    if dev <= 0.6:
        return 5.0
    return 3.0


def _score_abs(used, total):
    """覆盖型打分：用了几种手法 / 共几种。"""
    if not total:
        return None
    return round(min(used / total, 1.0) * 10, 1)


def _score_cust(ours, anchors):
    """绝对锚点打分：anchors = [(阈值, 分数)...] 阈值升序，取第一个达标的。"""
    if ours is None:
        return None
    for th, sc in anchors:
        if ours <= th:
            return sc
    return anchors[-1][1] if anchors else None


def _vm_with_examples(d):
    """单场视觉量化 + 案例帧（用本场的 frames 标注）。"""
    v = rj(pathlib.Path(d) / "visual.json")
    if not v:
        return None
    frames = v.get("frames") or []
    sources = [(frames, str(pathlib.Path(d).relative_to(ROOT)).replace("\\", "/"), "")]
    v["examples"] = attach_examples(v.get("dist"), sources)
    prop_ex = {}
    for x in (v.get("props") or [])[:5]:
        exs = []
        for f in frames:
            pp = f.get("props")
            if isinstance(pp, str):
                pp = [t.strip() for t in re.split(r"[、,，/]", pp)]
            pp = [str(t) for t in (pp or []) if str(t).strip()]
            if x["v"] in pp and f.get("sec") is not None:
                exs.append({"path": str(pathlib.Path(d).relative_to(ROOT)).replace("\\", "/"),
                            "sec": f["sec"], "name": ""})
                break
        if exs:
            prop_ex[x["v"]] = exs
    v["prop_examples"] = prop_ex
    return v


def target_diagnosis(path):
    """靶向诊断：一场我方直播 vs 同品类标杆靶心。
    """
    ss = {s["path"]: s for s in scan()}
    sa = ss.get(path)
    if not sa:
        return {"error": "场次不存在"}
    cat = sa["category"]
    bench = [x for x in scan()
             if x["category"] == cat and x["group"] == "benchmark" and x["analyzed"]]
    if not bench:
        return {"error": "该品类还没有标杆场次，先采集标杆"}
    n_b = len(bench)

    def avg_over(bench_sessions, fname, get):
        vals = []
        for x in bench_sessions:
            v = get(rj(x["dir"] / fname) or {})
            if v is not None:
                vals.append(v)
        if not vals:
            return None
        return round(sum(vals) / len(vals), 1)

    an = rj(sa["dir"] / "analysis.json") or {}
    m = an.get("metrics", {})
    tc = an.get("tag_count", {})
    tk = rj(sa["dir"] / "talk.json")
    vs = rj(sa["dir"] / "visual.json")
    rd = rj(sa["dir"] / "rounds.json")
    dp = rj(sa["dir"] / "deep.json")

    def vd(k):
        return ((vs or {}).get("derived") or {}).get(k, {})

    def vnum(k, pct=False):
        x = vd(k)
        v = x.get("value", x.get("pct"))
        return v

    def bench_talk(group, key):
        return avg_over(bench, "talk.json",
                        lambda x: (x.get("groups") or {}).get(group, {}).get(key))
    def bench_der(k):
        return avg_over(bench, "visual.json",
                        lambda x: (((x.get("derived") or {}).get(k) or {})
                                   .get("value", ((x.get("derived") or {}).get(k) or {}).get("pct"))))
    def bench_met(key):
        return avg_over(bench, "analysis.json", lambda x: x.get("metrics", {}).get(key))
    def bench_nrounds():
        return avg_over(bench, "rounds.json", lambda x: x.get("n_rounds"))

    def verdict(ours, b, dir_hi, tol=0.15):
        if ours is None or b is None:
            return None, "", ""
        gap = round(ours - b, 1)
        if dir_hi:
            bad = gap < -b * tol
            good = gap >= 0
        else:
            bad = gap > b * tol
            good = gap <= 0
        return gap, ("ok" if good else ("bad" if bad else "flat")), ""

    items = []
    def add(level, dim, ours, b, unit="", note="", score=None):
        """score=None 时自动按相对标杆打分；也可显式传入。"""
        if ours is None:
            return
        o_s = (("%.1f" % ours) + unit) if isinstance(ours, (int, float)) else str(ours)
        b_s = (("%.1f" % b) + unit) if isinstance(b, (int, float)) else (str(b) if b is not None else "—")
        gap, state, _ = (verdict(ours, b, True)
                         if isinstance(ours, (int, float)) and isinstance(b, (int, float))
                         else (None, "flat", ""))
        sc = score if score is not None else _score_rel(ours, b, True)
        items.append({"level": level, "dim": dim, "ours": ours, "bench": b,
                      "ours_s": o_s, "bench_s": b_s, "gap": gap,
                      "state": state or "flat", "note": note,
                      "score": round(sc, 1) if sc is not None else None})

    lm = lang_metrics(sa["dir"] / "transcript.json", cat)
    bench_lm = {"filler": [], "term": []}
    for x in bench:
        y = lang_metrics(x["dir"] / "transcript.json", cat)
        if y:
            bench_lm["filler"].append(y["filler_rate"])
            bench_lm["term"].append(y["term_rate"])
    b_filler = round(sum(bench_lm["filler"]) / len(bench_lm["filler"]), 1) if bench_lm["filler"] else None
    b_term = round(sum(bench_lm["term"]) / len(bench_lm["term"]), 1) if bench_lm["term"] else None

    add("外部形象", "出镜率（有人物帧）", vnum("有人物帧占比"), bench_der("有人物帧占比"),
        "%", "画面里有人 vs 商品特写；标杆普遍 80%+")
    add("外部形象", "直视镜头率", vnum("直视镜头率"), bench_der("直视镜头率"),
        "%", "镜头感：多少时间看着镜头讲")
    add("外部形象", "站位一致性", vnum("站位一致性"), bench_der("站位一致性"),
        "%", "机位/站位稳定度")
    add("外部形象", "手持产品率", vnum("手持产品率"), bench_der("手持产品率"),
        "%", "实物在手的讲解时间占比")
    spd = m.get("语速_字每分")
    add("基础表达", "语速（字/分）", spd, bench_met("语速_字每分"),
        "", "过慢显拖沓、过快听众跟不上；标杆区间约 300-350",
        score=_score_dev(spd, bench_met("语速_字每分") or 330, tol=0.2) if spd else None)
    eff = round(m.get("有效说话_秒", 0) / m["时长_秒"] * 100, 1) if m.get("时长_秒") else None
    add("基础表达", "有效说话占比", eff, None, "%", "剔除静音后的说话时间占比",
        score=_score_cust((100 - eff) if eff is not None else None,
                          [(3, 9.5), (6, 8.5), (10, 7.0), (18, 5.0), (99, 3.0)])
        if eff is not None else None)
    if lm:
        add("基础表达", "填充词率（次/千字）", lm["filler_rate"], b_filler,
            "", "嗯/啊/那个/这个等口头语，越低越流畅",
            score=_score_cust(lm["filler_rate"],
                              [(19, 9.5), (31, 8.0), (50, 6.5), (75, 5.0), (9999, 3.0)]))
    if tk and tk.get("groups"):
        g = tk["groups"]
        add("核心销售", "塑品话术占比", g["塑品"]["pct"], bench_talk("塑品", "pct"),
            "%", "讲透产品的时间；贴近标杆为佳",
            score=_score_dev(g["塑品"]["pct"], bench_talk("塑品", "pct") or g["塑品"]["pct"]))
        add("核心销售", "逼单话术占比", g["逼单"]["pct"], bench_talk("逼单", "pct"),
            "%", "促单密度；贴近标杆为佳",
            score=_score_dev(g["逼单"]["pct"], bench_talk("逼单", "pct") or g["逼单"]["pct"]))
        add("核心销售", "互动话术占比", g["互动"]["pct"], bench_talk("互动", "pct"),
            "%", "过高=陪聊，过低=不管观众",
            score=_score_dev(g["互动"]["pct"], bench_talk("互动", "pct") or g["互动"]["pct"]))
    pg = (tk or {}).get("push_gap")
    b_gap = avg_over(bench, "talk.json", lambda x: (x.get("push_gap") or {}).get("max_sec"))
    add("核心销售", "最长促单空窗（秒）", (pg or {}).get("max_sec"), b_gap,
        "", "连续没人提促单的最长时间",
        score=_score_rel((pg or {}).get("max_sec"), b_gap, hi=False) if (pg or {}).get("max_sec") else None)
    cta = m.get("行动指令_次每分")
    add("核心销售", "行动指令密度（次/分）", cta, bench_met("行动指令_次每分"),
        "", "引导下单的意识强度",
        score=_score_cust(cta, [(0.6, 3.0), (0.9, 5.0), (1.2, 7.5), (1.6, 9.0), (999, 10)])
        if cta is not None else None)
    def _cov(v):
        try:
            a, b = str(v).split("/")
            return int(a), int(b)
        except Exception:
            return None, None
    su, st = _cov(m.get("销售力_标签覆盖"))
    add("核心销售", "销售力手法覆盖", m.get("销售力_标签覆盖"), None, "",
        "价格锚定/堆叠/算账/稀缺/限时等用了几种",
        score=_score_abs(su, st))
    pu, pt = _cov(m.get("产品力_标签覆盖"))
    add("核心销售", "产品力手法覆盖", m.get("产品力_标签覆盖"), None, "",
        "专业度传达：参数/演示/对比/异议消解",
        score=_score_abs(pu, pt))
    if lm:
        add("核心销售", "术语密度（次/千字）", lm["term_rate"], b_term,
            "", "品类专业词频次；过低=讲不透，含 " +
            "、".join(t["term"] for t in lm["term_top"][:4]),
            score=_score_cust(lm["term_rate"],
                              [(999, 9.5)] if not b_term else
                              [(b_term * 0.5, 4.0), (b_term * 0.75, 6.0),
                               (b_term * 1.0, 8.0), (b_term * 1.25, 9.5), (99999, 10)]))
    add("高阶结构", "讲解轮次（轮）", (rd or {}).get("n_rounds"), bench_nrounds(),
        "", "整场切出的商品讲解循环数；过少=排品单薄")
    if dp:
        cp = (dp.get("script") or {}).get("core_point") or {}
        rep = cp.get("repeat")
        add("高阶结构", "核心卖点重复次数", rep, None, "",
            "主推卖点是否反复强化",
            score=_score_cust(rep, [(2, 3.0), (4, 5.5), (6, 7.5), (9, 9.0), (999, 10)])
            if rep is not None else None)
        chains = (dp.get("script") or {}).get("logic_chains") or []
        add("高阶结构", "卖点论证链条数", len(chains), None, "",
            "每个卖点是否配了论证（FAB/对比/演示…）",
            score=_score_cust(len(chains), [(2, 3.0), (4, 5.5), (6, 7.5), (9, 9.0), (999, 10)]))
    if vs and vs.get("props"):
        add("高阶结构", "道具使用密度", vnum("道具使用密度"), bench_der("道具使用密度"),
            " 件/帧", "服化道运用的丰富度")

    DIR_HI = {"出镜率（有人物帧）": True, "直视镜头率": True, "站位一致性": True,
              "手持产品率": True, "语速（字/分）": True, "有效说话占比": True,
              "最长促单空窗（秒）": False, "行动指令密度（次/分）": True,
              "讲解轮次（轮）": True, "核心卖点重复次数": True,
              "卖点论证链条数": True, "道具使用密度": True}
    worst = []
    for it in items:
        if it["gap"] is None or it["state"] != "bad":
            continue
        hi = DIR_HI.get(it["dim"], True)
        score = abs(it["gap"]) / max(abs(it["bench"]) or 1, 1)
        worst.append((score, it, hi))
    worst.sort(key=lambda x: -x[0])
    ADVICE = {
        "直视镜头率": "训练盯镜头：回放自己的直播，每次低头看货后立刻回镜；贴提示条于镜头旁",
        "出镜率（有人物帧）": "增加出镜讲解时间：商品特写镜头单次不超过 20 秒，切回真人承接",
        "手持产品率": "多用实物：讲解卖点时把产品拿在手上指给观众看，而不是只靠贴片",
        "站位一致性": "固定机位与站位：地面贴位标记，讲解时不游离出画面主体位",
        "最长促单空窗（秒）": "缩短无人促单的时间：每 5 分钟至少一次下单引导（报价格/库存/截止）",
        "行动指令密度（次/分）": "加频下单指令：'点几号链接''拍下备注'这类明确指令每分钟至少 1 次",
        "讲解轮次（轮）": "增加排品轮次：单场规划更多商品讲解循环，避免整场只盘一两款",
        "核心卖点重复次数": "核心卖点一小时内至少重复 3 次，换说法但不换点",
        "卖点论证链条数": "卖点必须带论证：参数→好处→演示/对比，不允许只报参数",
        "道具使用密度": "补充道具：价格牌、信息板、检测报告至少两件常驻桌面",
    }
    actions = []
    for score, it, hi in worst[:3]:
        actions.append({
            "dim": it["dim"], "ours": it["ours_s"], "bench": it["bench_s"],
            "gap": it["gap"],
            "advice": ADVICE.get(it["dim"], "对照标杆场次回放，逐段找差距"),
        })

    structure = None
    if tk and tk.get("groups"):
        bt = bench_talk("塑品", "pct"), bench_talk("互动", "pct"), bench_talk("逼单", "pct")
        if all(b is not None for b in bt):
            g = tk["groups"]
            structure = round(abs(g["塑品"]["pct"] - bt[0]) +
                              abs(g["互动"]["pct"] - bt[1]) +
                              abs(g["逼单"]["pct"] - bt[2]), 1)
    WEIGHT = {"外部形象": 0.15, "基础表达": 0.15, "核心销售": 0.40, "高阶结构": 0.30}
    level_scores = {}
    for lv in ("外部形象", "基础表达", "核心销售", "高阶结构"):
        xs = [it["score"] for it in items if it["level"] == lv and it.get("score") is not None]
        level_scores[lv] = round(sum(xs) / len(xs), 1) if xs else None
    total = None
    wsum, acc = 0.0, 0.0
    for lv, w in WEIGHT.items():
        sc = level_scores.get(lv)
        if sc is not None:
            acc += sc * w
            wsum += w
    if wsum:
        total = round(acc / wsum, 1)
    return {
        "path": path, "name": sa["name"], "category": cat, "bench_n": n_b,
        "bench_names": [x["name"] for x in bench][:8],
        "items": items, "actions": actions, "structure_gap": structure,
        "levels": ["外部形象", "基础表达", "核心销售", "高阶结构"],
        "level_scores": level_scores, "total_score": total,
        "lang": {"term_top": (lm or {}).get("term_top", [])},
    }


def vfield(f, dim, key):
    """安全取视觉标注枚举值（模型偶尔把维度写成字符串）。"""
    v = f.get(dim)
    if isinstance(v, dict):
        v = v.get(key)
    return str(v).strip() if v is not None else ""


def visual_examples(field, val_frames, max_n=3):
    """给"某字段某取值"找代表性截图帧。val_frames = [(frames列表, path, name)]。
    优先从不同场次取帧（多场印证比单场三帧更有说服力），取值必须精确匹配。
    """
    per_session = {}
    for frames, spath, name in val_frames:
        for f in frames:
            if vfield(f, *field.split(".", 1)) == val_frames and False:
                pass
    return []


def vex_for(field_key, value, sources, max_n=3):
    """sources = [(frames, path, name)]；返回该字段该取值的案例帧列表。
    每个场次最多取 1 帧，跨场次取证；帧选择偏向中段（开场/结尾常不典型）。
    """
    out = []
    for frames, spath, name in sources:
        hits = [f.get("sec") for f in frames
                if vfield(f, *field_key.split(".", 1)) == value and f.get("sec") is not None]
        if not hits:
            continue
        sec = hits[len(hits) // 2]
        out.append({"path": spath, "sec": sec, "name": name})
        if len(out) >= max_n:
            break
    return out


def attach_examples(dist, sources, top_n=3, per_val=3):
    """给分布 dict（field -> [取值列表]）的每个取值附案例帧。只给 top 值配，控制数据量。"""
    out = {}
    for field, arr in (dist or {}).items():
        for x in arr[:top_n]:
            ex = vex_for(field, x["v"], sources, per_val)
            if ex:
                out.setdefault(field, {})[x["v"]] = ex
    return out


def playbook(category):
    """标杆逐场拆解：把每场标杆的讲解逻辑、讲品结构、卖点数量、逼单方式
    的**具体内容**（含原话和时间戳）逐场列出 —— 这是可以直接学的素材库，
    与 method.py 的跨场归纳互补。
    """
    from collections import Counter
    ss = [x for x in scan()
          if x["category"] == category and x["group"] == "benchmark" and x["analyzed"]]
    sessions = []
    urg_counter = Counter()
    for x in ss:
        dp = rj(x["dir"] / "deep.json") or {}
        sc = dp.get("script") or {}
        an = rj(x["dir"] / "analysis.json") or {}
        ev = an.get("evidence") or {}
        m = an.get("metrics", {})
        names = taxo()["dims"]["sales"]["tags"]
        def ev_of(tag, n=3):
            return [(e.get("text", "") or "")[:80] for e in (ev.get("sales." + tag) or [])
                    if e.get("text")]
        urgency = [{"tag": t, "name": names[t]["name"], "examples": ev_of(t)}
                   for t in ("urgent", "scarce", "crowd", "loss") if ev_of(t)]
        price_ex = [{"tag": t, "name": names[t]["name"], "examples": ev_of(t)}
                    for t in ("anchor", "calc", "stack") if ev_of(t)]
        cta_ex = ev_of("cta", 3)
        chains = sc.get("logic_chains") or []
        core = sc.get("core_point") or {}
        st = sc.get("structure") or {}
        audience = ((dp.get("audience") or {}).get("concerns") or [])[:]
        audience.sort(key=lambda c: -c.get("n", 0))
        meth = Counter(c.get("method", "") for c in chains if c.get("method"))
        sessions.append({
            "path": x["path"], "name": x["name"], "started": x["started"],
            "elapsed": x["elapsed"],
            "flow": st.get("flow", []),
            "cycle": st.get("cycle", ""),
            "audience": [{"topic": c.get("topic", ""), "n": c.get("n", 0),
                          "answered": c.get("answered"),
                          "samples": (c.get("samples") or [])[:3]}
                         for c in audience[:6]],
            "chains": [{"point": c.get("point", ""), "method": c.get("method", ""),
                        "how": c.get("how", ""), "quote": c.get("quote", ""),
                        "t": c.get("t", 0)} for c in chains],
            "n_chains": len(chains),
            "methods": [{"m": k, "n": v} for k, v in meth.most_common(6)],
            "core_what": core.get("what", ""), "core_repeat": core.get("repeat", 0),
            "price_examples": price_ex, "urgency": urgency, "cta_examples": cta_ex,
            "cta_per_min": m.get("行动指令_次每分"),
        })
        for u in urgency:
            urg_counter[u["name"]] += 1
    topic_counter = Counter()
    topic_answered = {}
    for x in ss:
        for c in ((rj(x["dir"] / "deep.json") or {}).get("audience") or {}).get("concerns", []):
            key = c.get("topic", "")
            if not key:
                continue
            topic_counter[key] += c.get("n", 0)
            topic_answered.setdefault(key, []).append(bool(c.get("answered")))
    concerns_rank = [{"topic": k, "n": v,
                      "answered_rate": round(sum(topic_answered[k]) /
                                             len(topic_answered[k]) * 100)}
                     for k, v in topic_counter.most_common(12)]
    return {"category": category, "n": len(sessions), "sessions": sessions,
            "urgency_rank": [{"u": k, "n": v} for k, v in urg_counter.most_common(8)],
            "concerns_rank": concerns_rank}


ANCHORS = ROOT / "config" / "anchors.json"


def anchors_cfg():
    return rj(ANCHORS) or {}


def session_anchors(rid, started):
    """该场次的上播主播：采集日期 == 排班/实绩日期。"""
    ac = anchors_cfg().get("by_room", {}).get(rid) or {}
    day = (started or "")[:10].replace("_", "-")
    out = []
    for name, a in (ac.get("anchors") or {}).items():
        if day in (a.get("dates") or []):
            out.append({"name": name, "shift": (a.get("shifts") or [""])[0]})
    return out


def anchor_view(sel_name=None):
    """主播维度分析：上播信息（config/anchors.json 的排班）× 已分析场次评分。
    """
    ac = anchors_cfg().get("by_room", {})
    roster = {}
    for rid, e in ac.items():
        for name, a in (e.get("anchors") or {}).items():
            r = roster.setdefault(name, {"name": name, "source": e.get("source"),
                                         "rooms": set(), "dates": set(), "n": 0})
            r["rooms"].add(e.get("name", rid))
            for dt in (a.get("dates") or []):
                r["dates"].add(dt)
            r["n"] += len(a.get("dates") or [])
    anchors = [{"name": k, "source": v["source"],
                "rooms": sorted(v["rooms"]), "n_dates": len(v["dates"]),
                "last": max(v["dates"]) if v["dates"] else ""}
               for k, v in roster.items()]
    anchors.sort(key=lambda x: -x["n_dates"])

    sel = None
    if sel_name and sel_name in roster:
        r = roster[sel_name]
        rid_map = {e.get("name"): rid for rid, e in ac.items()}
        his_rooms = {rid: e for rid, e in ac.items()
                     if sel_name in (e.get("anchors") or {})}
        sess = []
        for rid, e in his_rooms.items():
            dates = (e["anchors"][sel_name] or {}).get("dates") or []
            for x in scan():
                if x["room"] != rid:
                    continue
                day = x["started"][:4] + "-" + x["started"][4:6] + "-" + x["started"][6:8]
                if day not in dates:
                    continue
                tg = target_diagnosis(x["path"]) or {}
                items = tg.get("items") or []
                sess.append({
                    "path": x["path"], "name": x["name"], "started": x["started"],
                    "elapsed": x["elapsed"], "analyzed": x["analyzed"],
                    "total_score": tg.get("total_score"),
                    "level_scores": tg.get("level_scores", {}),
                    "worst": (min((i for i in items if i.get("score") is not None),
                                  key=lambda i: i["score"])["dim"]
                              if any(i.get("score") is not None for i in items) else ""),
                })
        sess.sort(key=lambda x: -x.get("elapsed", 0))
        sel = {"name": sel_name,
               "rooms": sorted(r["rooms"]), "n_dates": len(r["dates"]),
               "dates": sorted(r["dates"], reverse=True)[:30],
               "sessions": sess}
    return {"anchors": anchors, "sel": sel}


def visual_profile(category, group=None):
    """同品类视觉画像：把各场的枚举分布合并，得到"这个品类的标杆长什么样"。
    这是量化的落点 —— 单场分布只能描述，多场合并才能定标准和比差距。
    """
    from collections import Counter, defaultdict
    ss = [s for s in scan() if s["category"] == category and s["analyzed"]]
    if group:
        ss = [s for s in ss if s["group"] == group]
    vs = []
    for s in ss:
        v = rj(s["dir"] / "visual.json")
        if v:
            vs.append((s, v))
    if not vs:
        return {"category": category, "n": 0, "dist": {}, "derived": [], "props": []}
    agg = defaultdict(Counter)
    tot = 0
    props = Counter()
    prop_tot = 0
    for s, v in vs:
        n = v.get("n_frames", 0)
        tot += n
        for k, arr in (v.get("dist") or {}).items():
            for x in arr:
                agg[k][x["v"]] += x["n"]
        for x in (v.get("props") or []):
            props[x["v"]] += x["n"]
        prop_tot += n
    dist = {}
    for k, c in agg.items():
        dist[k] = [{"v": kk, "n": nn, "pct": round(nn / tot * 100)}
                   for kk, nn in c.most_common()]
    keys = ["站位一致性", "妆造强度分", "道具使用密度", "手持产品率",
            "直视镜头率", "视觉信息密度", "妆造变化次数", "有人物帧占比"]
    derived = []
    for k in keys:
        vals = []
        for s, v in vs:
            d0 = (v.get("derived") or {}).get(k) or {}
            x = d0.get("value", d0.get("pct"))
            if isinstance(x, (int, float)):
                vals.append(x)
        if vals:
            derived.append({"key": k, "avg": round(sum(vals) / len(vals), 2),
                            "min": min(vals), "max": max(vals), "n": len(vals)})
    ret = {
        "category": category, "n": len(vs), "frames": tot,
        "sessions": [{"name": s["name"], "started": s["started"], "path": s["path"],
                      "group": s["group"]} for s, _ in vs],
        "dist": dist,
        "props": [{"v": k, "n": v, "pct": round(v / prop_tot * 100)}
                  for k, v in props.most_common()],
        "derived": derived,
    }
    sources = [((v.get("frames") or []), s["path"], s["name"]) for s, v in vs]
    ret["examples"] = attach_examples(dist, sources)
    prop_src = []
    for s, v in vs:
        prop_src.append((v.get("frames") or [], s["path"], s["name"]))
    prop_ex = {}
    for x in ret["props"][:5]:
        exs = []
        for frames, spath, name in prop_src:
            for f in frames:
                pp = f.get("props")
                if isinstance(pp, str):
                    pp = [t.strip() for t in re.split(r"[、,，/]", pp)]
                pp = [str(t) for t in (pp or []) if str(t).strip()]
                if x["v"] in pp and f.get("sec") is not None:
                    exs.append({"path": spath, "sec": f["sec"], "name": name})
                    break
            if len(exs) >= 3:
                break
        if exs:
            prop_ex[x["v"]] = exs
    ret["prop_examples"] = prop_ex
    return ret


def bench_cats():
    """有已分析标杆的品类列表，前端标杆库页的切换下拉用它。"""
    from collections import Counter
    c = Counter(s["category"] for s in scan()
                if s["analyzed"] and s["group"] == "benchmark")
    return [{"category": k, "n": v} for k, v in c.most_common()]


def talk_cats():
    from collections import Counter
    c = Counter()
    for s in scan():
        if rj(s["dir"] / "talk.json"):
            c[s["category"]] += 1
    return [{"category": k, "n": v} for k, v in c.most_common()]


def visual_cats():
    """有视觉量化数据的品类（前端视觉页的切换下拉用）。"""
    from collections import Counter
    c = Counter()
    for s in scan():
        if s["analyzed"] and rj(s["dir"] / "visual.json"):
            c[(s["category"], s["group"])] += 1
    cats = {}
    for (cat, grp), n in c.items():
        cats.setdefault(cat, {"category": cat, "bench_n": 0, "own_n": 0})
        if grp == "benchmark":
            cats[cat]["bench_n"] += n
        elif grp == "own":
            cats[cat]["own_n"] += n
    return sorted(cats.values(), key=lambda x: -(x["bench_n"] + x["own_n"]))


def method_cats():
    """列出有样本的品类，标注是否已生成方法论。前端方法论页的品类下拉用它。"""
    cfg = rooms_cfg()
    by_cat = {}
    for s in scan():
        if not s["analyzed"]:
            continue
        c = s["category"]
        d = by_cat.setdefault(c, {"category": c, "bench_n": 0, "own_n": 0, "has": False})
        if s["group"] == "benchmark":
            d["bench_n"] += 1
        elif s["group"] == "own":
            d["own_n"] += 1
    for c, d in by_cat.items():
        f = METHOD_DIR / ("%s.json" % c.replace("/", "_"))
        d["has"] = f.exists()
        if f.exists():
            m = rj(f, {}) or {}
            d["generated_at"] = m.get("generated_at", "")
    return sorted(by_cat.values(), key=lambda x: (-x["bench_n"], x["category"]))


def method_load(cat):
    """读某品类的方法论。不传 cat 时给一个默认（样本最多的品类）。"""
    cats = method_cats()
    if not cat:
        have = [c for c in cats if c["has"]]
        cat = (have or cats)[0]["category"] if (have or cats) else ""
    if not cat:
        return None
    f = METHOD_DIR / ("%s.json" % cat.replace("/", "_"))
    return rj(f)


SITE_FILE = ROOT / "config" / "site.json"


def site_meta():
    """config/site.json → <meta name=site>，页面顶栏 / 登录态 / 提需求接口从这里读。
    用 meta 而不是内联 <script>：部署平台的 CSP 不允许内联脚本。没有这个文件就是单机模式。
    """
    import html as _h
    cfg = rj(SITE_FILE) or {}
    return '<meta name=site content="%s">' % _h.escape(json.dumps(cfg, ensure_ascii=False), quote=True)


def split_page(html):
    """把单文件页面拆成 (html, js)。
    """
    html = html.replace("</head>", site_meta() + "\n</head>", 1)
    head, sep, rest = html.partition("<script>")
    assert sep, "dashboard.html 里没找到内联 <script>"
    js, sep2, tail = rest.rpartition("</script>")
    assert sep2, "dashboard.html 里没找到 </script>"
    assert "<script>" not in js, "内联 <script> 不止一个，拆分会错位"
    return head + '<script src="app.js"></script>' + tail, js


def page():
    return split_page(PAGE_FILE.read_text(encoding="utf-8"))[0]


def page_js():
    return split_page(PAGE_FILE.read_text(encoding="utf-8"))[1]


def tree_data(ss):
    """左树：按直播间聚合 + 只登记未采的也算一间。服务与静态导出共用。"""
    tree = {}
    for s in ss:
        t = tree.setdefault(s["room"], {
            "room": s["room"], "name": s["name"], "group": s["group"],
            "category": s["category"], "recording": False, "sessions": [],
            "total_elapsed": 0, "total_danmu": 0, "analyzed_n": 0,
            "dims": None, "risk": 0})
        t["recording"] = t["recording"] or not s["done"]
        t["total_elapsed"] += s["elapsed"]
        t["total_danmu"] += s["danmu"]
        t["sessions"].append({"path": s["path"], "started": s["started"],
                              "elapsed": s["elapsed"], "analyzed": s["analyzed"],
                              "danmu": s["danmu"], "chars": s["chars"],
                              "has_report": s["has_report"]})
        if s["analyzed"]:
            t["analyzed_n"] += 1
            an = rj(s["dir"] / "analysis.json")
            if an and t["dims"] is None:
                t["dims"] = dim_cover(an)
                m = an.get("metrics", {})
                t["risk"] = (m.get("口播风险话术", 0) or 0) + (m.get("弹幕风险话术", 0) or 0)
    seen = set(tree)
    for rid, r in rooms_cfg().items():
        if rid not in seen:
            tree[rid] = {"room": rid, "name": r.get("name", rid),
                         "group": r.get("group", "unknown"),
                         "category": r.get("category", "未分类"),
                         "recording": False, "sessions": [], "total_elapsed": 0,
                         "total_danmu": 0, "analyzed_n": 0, "dims": None, "risk": 0}
    pend = (rj(ROOMS) or {}).get("own_pending", {}).get("list", [])
    return list(tree.values()), pend


def cmp_payload(s):
    """对标分析所需的场次数据（客户端可直接算出差异清单）。"""
    an = rj(s["dir"] / "analysis.json") or {}
    ev = an.get("evidence") or {}
    slim = {}
    for k, arr in ev.items():
        slim[k] = [{"t": e.get("t", 0), "text": (e.get("text") or "")[:90],
                    "hit": (e.get("hit") or [])[:3]} for e in (arr or [])[:2]]
    return {"name": s["name"], "started": s["started"], "group": s["group"],
            "elapsed": s["elapsed"], "category": s["category"],
            "tag_count": an.get("tag_count", {}), "metrics": an.get("metrics", {}),
            "evidence": slim}


DOC_ROOTS = {
    "qabook": (ROOT / "data" / "qabook", "消费者问题手册",
               "弹幕里的真实提问 + 标杆的真实答法，给新人直接背"),
    "drill": (ROOT / "data" / "drill", "训练卡",
              "这周练什么、照谁练、练到多少算过"),
    "audit": (ROOT / "data" / "audit", "视觉标注抽检",
              "人工复核模型标注，算字段级准确率"),
}


def doc_manifest():
    """交付物清单 —— 前端 #/docs 页只读这一份。
    """
    groups = []
    hp = ROOT / "logs" / "health_latest.txt"
    if hp.exists():
        txt = hp.read_text(encoding="utf-8", errors="replace")
        first = next((ln.strip() for ln in txt.splitlines() if ln.strip()), "")
        groups.append({"key": "health", "title": "产出体检",
                       "desc": "每天一份，回答系统到底产出了什么",
                       "items": [{"key": "health", "file": "health", "name": "最新体检报告",
                                  "kind": "txt", "meta": first, "mtime": int(hp.stat().st_mtime),
                                  "pass": txt.count("[PASS]"), "warn": txt.count("[WARN]"),
                                  "fail": txt.count("[FAIL]")}]})
    for key, (d, title, desc) in DOC_ROOTS.items():
        items = []
        if d.exists():
            for f in sorted(d.iterdir()):
                suf = f.suffix.lower()
                if suf not in (".md", ".html"):
                    continue
                meta = ""
                if suf == ".md":
                    for ln in f.read_text(encoding="utf-8", errors="replace").splitlines()[:8]:
                        if ln.startswith(">"):
                            meta = re.sub(r"\*\*(.+?)\*\*", r"\1", ln[1:]).strip()
                            break
                items.append({"key": key, "file": f.name, "name": f.stem, "kind": suf[1:],
                              "meta": meta, "mtime": int(f.stat().st_mtime)})
        if items:
            groups.append({"key": key, "title": title, "desc": desc, "items": items})
    return groups


def md_to_html(text):
    """够用就好的 Markdown 渲染：这些 md 都是本项目自己生成的，格式可控，
    不值得为它引一个依赖。只认标题、表格、列表、粗体、引用。
    """
    import html as _h
    out, in_tbl = [], False
    for raw in text.splitlines():
        ln = raw.rstrip()
        if ln.startswith("|") and ln.endswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if all(set(c) <= set("-: ") and c for c in cells):
                continue
            tag = "th" if not in_tbl else "td"
            if not in_tbl:
                out.append("<table>")
                in_tbl = True
            out.append("<tr>" + "".join(
                "<%s>%s</%s>" % (tag, re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", _h.escape(c)), tag)
                for c in cells) + "</tr>")
            continue
        if in_tbl:
            out.append("</table>")
            in_tbl = False
        if not ln.strip():
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, _h.escape(m.group(2)), lvl))
            continue
        if ln.startswith(">"):
            q = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>",
                       _h.escape(ln[1:].strip()))
            out.append("<blockquote>%s</blockquote>" % q)
            continue
        if ln.startswith("---"):
            out.append("<hr>")
            continue
        body = _h.escape(ln.lstrip().lstrip("- ").rstrip())
        body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
        if ln.lstrip().startswith("- "):
            sub = " class=sub" if ln.startswith("  ") else ""
            if not out or not out[-1].startswith("<li"):
                out.append("<ul>")
            out.append("<li%s>%s</li>" % (sub, body))
            continue
        out.append("<p>%s</p>" % body)
    if in_tbl:
        out.append("</table>")
    res, open_ul = [], False
    for x in out:
        if x == "<ul>":
            open_ul = True
        elif open_ul and not x.startswith("<li"):
            res.append("</ul>")
            open_ul = False
        res.append(x)
    if open_ul:
        res.append("</ul>")
    return "\n".join(res)


DOC_CSS = """
@font-face{font-family:'DM Sans';font-weight:400 700;font-display:swap;src:url('/hb/assets/fonts/DMSans.woff2') format('woff2')}
:root{--bg:#f5f6f9;--pnl:#fff;--pnl2:#f4f6f9;--pnl3:#e9edf3;--fg:#0b1220;--fg2:#334155;--dim:#64748b;
 --line:#e6eaf0;--line2:#d4dbe5;--acc:#3f6bff;--acc-soft:#eef2ff;--acc-ink:#2a4fd6;--ok:#059669;--warn:#b45309;--bad:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#0a0c11;--pnl:#12161e;--pnl2:#171c26;--pnl3:#202733;--fg:#e9edf4;
 --fg2:#c3ccda;--dim:#8a93a5;--line:#222a36;--line2:#2f3947;--acc:#5b8bff;--acc-soft:#16213b;--acc-ink:#8fb0ff;
 --ok:#2fcf94;--warn:#f0aa46;--bad:#ff6b6b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);-webkit-font-smoothing:antialiased;
 font:15px/1.8 'DM Sans',-apple-system,'PingFang SC','Microsoft YaHei',system-ui,sans-serif}
.top{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:12px;height:56px;padding:0 22px;
 background:var(--pnl);border-bottom:1px solid var(--line)}
.top b{font-size:14px}
.back{display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 12px;border-radius:9px;border:1px solid var(--line);
 background:var(--pnl);color:var(--fg2);font-size:13px;text-decoration:none}
.back:hover{border-color:var(--line2);color:var(--fg)}
.wrap{max-width:920px;margin:24px auto 80px;padding:30px 36px 40px;background:var(--pnl);border:1px solid var(--line);border-radius:18px}
@media (max-width:700px){.wrap{margin:12px;padding:20px 18px}}
a{color:var(--acc)}
h1{font-size:26px;letter-spacing:-.03em;line-height:1.3;margin:0 0 12px}
h2{font-size:19px;letter-spacing:-.02em;margin:34px 0 12px;padding-top:14px;border-top:1px solid var(--line)}
h3{font-size:16px;margin:24px 0 8px}
p{margin:8px 0;color:var(--fg2)}
strong{color:var(--fg)}
ul{margin:8px 0;padding-left:20px}
li{margin:4px 0;color:var(--fg2)}
li.sub{list-style:none;margin-left:-4px;padding:8px 12px;border-radius:10px;background:var(--pnl2);font-size:14px}
table{border-collapse:separate;border-spacing:0;margin:12px 0;width:100%;border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{padding:9px 12px;text-align:left;font-size:14px;border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:0}
th{background:var(--pnl2);color:var(--dim);font-size:12.5px;font-weight:650}
blockquote{margin:12px 0;padding:10px 14px;border-radius:12px;background:var(--acc-soft);color:var(--acc-ink);font-size:14px}
hr{border:0;border-top:1px dashed var(--line2);margin:26px 0}
pre{white-space:pre-wrap;font:13px/1.75 'JetBrains Mono',ui-monospace,Consolas,monospace;margin:0}
.mut{color:var(--dim);font-size:13px}
"""


def doc_page(title, inner, back):
    """独立文档页（旧链接直达 / 从应用里"新页面打开"）。本地与静态导出共用。"""
    import html as _h
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>%s · 交付物</title><style>%s</style></head><body>"
            '<div class=top><a class=back href="%s">&larr; 交付物</a><b>%s</b></div>'
            "<div class=wrap>%s</div></body></html>"
            % (_h.escape(title), DOC_CSS, back, _h.escape(title), inner))


def session_path(rel):
    """把请求参数里的场次相对路径落成真实目录，越界返回 None。
    """
    if not rel:
        return None
    try:
        d = (ROOT / rel).resolve()
        d.relative_to(DATA.resolve())
    except Exception:
        return None
    return d if d.is_dir() else None


def doc_resolve(rel):
    """把 /docs?f=<key>/<文件名> 解析成真实路径，越界一律拒绝。"""
    if rel == "health":
        p = ROOT / "logs" / "health_latest.txt"
        return p if p.exists() else None
    key, _, name = rel.partition("/")
    entry = DOC_ROOTS.get(key)
    if not entry or not name:
        return None
    base = entry[0].resolve()
    try:
        p = (base / name).resolve()
    except Exception:
        return None
    if p.parent != base or not p.is_file():
        return None
    if p.suffix.lower() not in (".md", ".html", ".txt"):
        return None
    return p


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _q(self, k, d=""):
        from urllib.parse import parse_qs, urlparse
        return parse_qs(urlparse(self.path).query).get(k, [d])[0]

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            return self._send(200, "text/html; charset=utf-8", page().encode("utf-8"))

        if p == "/hb/logo-mark.png":
            f = ROOT / "scripts" / "assets" / "logo-mark.png"
            if f.exists():
                return self._send(200, "image/png", f.read_bytes())
        if p == "/app.js":
            return self._send(200, "application/javascript; charset=utf-8",
                              page_js().encode("utf-8"))

        if p == "/api":
            ss = scan()
            view = self._q("view", "session")
            want = self._q("p")
            cur = next((s for s in ss if s["path"] == want), None)
            rec = [s for s in ss if not s["done"]]
            if cur is None:
                analyzed = [s for s in ss if s["analyzed"]]
                if view == "live" and rec:
                    cur = max(rec, key=lambda x: x["mtime"])
                else:
                    cur = (analyzed[0] if analyzed else
                           (max(rec, key=lambda x: x["mtime"]) if rec else
                            (ss[0] if ss else None)))
            q_cat, q_p, q_an = self._q("cat"), self._q("p"), self._q("an")
            if view == "method" and not q_cat:
                mc = method_cats()
                q_cat = mc[0]["category"] if mc else ""
            if view == "target" and not q_p:
                q_p = next((s["path"] for s in ss
                            if s["group"] == "own" and s["analyzed"]), "")
            if view == "anchor" and not q_an:
                al = anchor_view().get("anchors") or []
                q_an = al[0]["name"] if al else ""
            tree, pend = tree_data(ss)
            cat_v = self._q("cat") or (visual_cats()[0]["category"]
                                       if visual_cats() else "")
            body = {
                "tree": tree,
                "pending": pend,
                "recording": [{k: v for k, v in s.items() if k != "dir"} for s in rec],
                "all": [{"path": s["path"], "name": s["name"], "started": s["started"],
                         "group": s["group"], "analyzed": s["analyzed"]} for s in ss],
                "selected": cur["path"] if cur else "",
                "detail": session_detail(cur) if cur and view == "session" else None,
                "compare": compare(self._q("a"), self._q("b"))
                if view == "compare" and self._q("a") and self._q("b") else None,
                "bench": benchmark(self._q("cat") or (cur["category"] if cur else ""))
                if view == "bench" else None,
                "bench_cats": bench_cats() if view == "bench" else None,
                "method_cats": method_cats() if view == "method" else None,
                "playbook": playbook(q_cat or (cur["category"] if cur else ""))
                if view == "method" else None,
                "visual": visual_profile(
                    self._q("cat") or (visual_cats()[0]["category"]
                                       if visual_cats() else ""),
                    self._q("grp") or None)
                if view == "visual" else None,
                "visual_bench": visual_profile(cat_v, "benchmark") if view == "visual" and cat_v else None,
                "visual_own": visual_profile(cat_v, "own") if view == "visual" and cat_v else None,
                "visual_cats": visual_cats() if view == "visual" else None,
                "talk": talk_profile(self._q("tcat") or None,
                                     self._q("tgrp") or None)
                if view == "talk" else None,
                "talk_cats": talk_cats() if view == "talk" else None,
                "target": target_diagnosis(q_p)
                if view == "target" and q_p else None,
                "target_sessions": [{"path": s["path"], "name": s["name"],
                                     "category": s["category"]}
                                    for s in scan()
                                    if s["group"] == "own" and s["analyzed"]]
                if view == "target" else None,
                "anchor_list": anchor_view().get("anchors") if view == "anchor" else None,
                "anchor": anchor_view(q_an)["sel"]
                if view == "anchor" and q_an else None,
                "talk_all": talk_profile().get("rows") if view == "talk" else None,
                "method": method_load(q_cat) if view == "method" else None,
                "total": {"sessions": len(ss), "analyzed": sum(1 for s in ss if s["analyzed"]),
                          "danmu": sum(s["danmu"] for s in ss),
                          "chars": sum(s["chars"] for s in ss)},
            }
            return self._send(200, "application/json; charset=utf-8",
                              json.dumps(body, ensure_ascii=False).encode("utf-8"))

        if p == "/frame":
            base = session_path(self._q("p"))
            if base is None:
                return self._send(404, "text/plain", b"bad path")
            fdir = base / "frames"
            if fdir.exists():
                fs = sorted(fdir.glob("*.jpg"))
                if fs:
                    sec = self._q("s")
                    if sec:
                        try:
                            t = float(sec)
                            fs = [min(fs, key=lambda f: abs(int(f.stem) - t))]
                        except Exception:
                            pass
                    return self._send(200, "image/jpeg", fs[-1].read_bytes())
            return self._send(404, "text/plain", b"no frame")

        if p == "/frames":
            sd = session_path(self._q("p"))
            fdir = (sd / "frames") if sd else None
            fs = sorted(int(f.stem) for f in fdir.glob("*.jpg")) if fdir and fdir.exists() else []
            return self._send(200, "application/json; charset=utf-8",
                              json.dumps({"secs": fs}).encode("utf-8"))

        if p == "/docs":
            if self._q("json"):
                return self._send(200, "application/json; charset=utf-8",
                                  json.dumps(doc_manifest(), ensure_ascii=False).encode("utf-8"))
            rel = self._q("f")
            if not rel:
                self.send_response(302)
                self.send_header("Location", "/#/docs")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            f = doc_resolve(rel)
            if f is None:
                return self._send(404, "text/html; charset=utf-8",
                                  "<p>找不到这份文件</p>".encode("utf-8"))
            if f.suffix.lower() == ".html":
                return self._send(200, "text/html; charset=utf-8", f.read_bytes())
            body = f.read_text(encoding="utf-8", errors="replace")
            if self._q("raw"):
                return self._send(200, "text/plain; charset=utf-8", body.encode("utf-8"))
            import html as _h
            md = f.suffix.lower() == ".md"
            inner = md_to_html(body) if md else "<h1>产出体检</h1><pre>%s</pre>" % _h.escape(body)
            return self._send(200, "text/html; charset=utf-8",
                              doc_page(f.stem if md else "产出体检", inner, "/#/docs").encode("utf-8"))

        if p == "/report":
            sd = session_path(self._q("p"))
            f = (sd / "report.html") if sd else None
            if f and f.exists():
                return self._send(200, "text/html; charset=utf-8", f.read_bytes())
            return self._send(404, "text/html; charset=utf-8",
                              "<p>这场还没生成报告</p>".encode("utf-8"))
        self._send(404, "text/plain", b"404")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="直播话术分析平台")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    print("分析平台 http://localhost:%d" % a.port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
