#!/usr/bin/env python3
"""话术分项量化：把互动/塑品/逼单三类话术拆成占比与效率指标。

用法:
  python scripts/talk_metrics.py <录制目录>
"""
import json, pathlib, sys, time
from collections import Counter, defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
TAXO = ROOT / "config" / "taxonomy.json"

GROUPS = {
    "塑品": {
        "product.param", "product.benefit", "product.scene",
        "product.demo", "product.compare", "product.objection",
    },
    "互动": {
        "interact.ask", "interact.cmd", "interact.call",
        "interact.newbie", "interact.follow",
    },
    "逼单": {
        "sales.anchor", "sales.stack", "sales.calc", "sales.scarce",
        "sales.urgent", "sales.crowd", "sales.loss", "sales.cta", "sales.assure",
    },
}
LABEL = {"塑品": "塑品话术", "互动": "互动话术", "逼单": "逼单话术"}


def rj(p, d=None):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return d


def name_of():
    t = rj(TAXO) or {}
    return {dk + "." + tk: tv.get("name", tk)
            for dk, dv in (t.get("dims") or {}).items()
            for tk, tv in (dv.get("tags") or {}).items()}


MIN_STAT_POINTS = 10


def audience(d, segs):
    """在线人数曲线对到话术上：三类话术各自讲的时候，在线人数每分钟涨跌多少。
    """
    pts = []
    try:
        for ln in (d / "stats.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            if r.get("online") is not None:
                pts.append((r["t"], r["online"]))
    except Exception:
        return None
    if len(pts) < MIN_STAT_POINTS:
        return None
    pts.sort()

    def at(x):
        if x <= pts[0][0]:
            return pts[0][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if x <= t1:
                return v0 + (v1 - v0) * (x - t0) / (t1 - t0) if t1 > t0 else v1
        return pts[-1][1]

    vals = [v for _, v in pts]
    mean = sum(vals) / len(vals)
    by = {}
    for g, keys in GROUPS.items():
        delta = mins = 0.0
        for s in segs:
            if any((t["dim"] + "." + t["tag"]) in keys for t in (s.get("tags") or [])):
                delta += at(s["end"]) - at(s["start"])
                mins += s["dur"] / 60
        if mins >= 1 and mean > 0:
            by[g] = {"minutes": round(mins, 1),
                     "delta_pct_per_min": round(delta / mean / mins * 100, 2)}
    likes = []
    try:
        likes = [json.loads(ln) for ln in (d / "stats.jsonl").read_text(encoding="utf-8").splitlines()]
        likes = [(r["t"], r["likes"]) for r in likes if r.get("likes") is not None]
    except Exception:
        pass
    span = (likes[-1][0] - likes[0][0]) / 60 if len(likes) >= 2 else 0
    return {
        "points": len(pts),
        "online_mean": round(mean, 1),
        "online_median": sorted(vals)[len(vals) // 2],
        "online_peak": max(vals),
        "likes_per_min": round((likes[-1][1] - likes[0][1]) / span, 1) if span > 0 else None,
        "by_group": by,
        "note": "讲某类话术时在线人数每分钟变化（占场均在线的百分比）。"
                "在线主要受平台推流影响，要多场累积看方向，单场不下结论",
    }


def main(d):
    d = pathlib.Path(d).resolve()
    an = rj(d / "analysis.json")
    if not an:
        raise SystemExit("缺 analysis.json：%s" % d)
    segs = an.get("segments") or []
    if not segs:
        raise SystemExit("analysis.json 里没有 segments")

    total = sum(s["dur"] for s in segs)
    names = name_of()
    stats = {k: {"sec": 0.0, "n": 0, "tags": Counter(), "spans": []}
             for k in GROUPS}
    other = {"sec": 0.0, "n": 0}

    for s in segs:
        hit = set()
        for t in (s.get("tags") or []):
            key = t["dim"] + "." + t["tag"]
            for g, keys in GROUPS.items():
                if key in keys:
                    hit.add(g)
                    stats[g]["tags"][key] += 1
        if hit:
            share = s["dur"] / len(hit)
            for g in hit:
                stats[g]["sec"] += share
                stats[g]["n"] += 1
        else:
            other["sec"] += s["dur"]
            other["n"] += 1

    out = {"path": str(d.relative_to(ROOT)).replace("\\", "/"),
           "total_sec": round(total, 1),
           "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "groups": {}}

    for g, st in stats.items():
        spans = []
        cur = None
        for s in segs:
            in_g = any((t["dim"] + "." + t["tag"]) in GROUPS[g]
                       for t in (s.get("tags") or []))
            if in_g:
                if cur is None:
                    cur = [s["start"], s["end"], s["dur"]]
                else:
                    cur[1] = s["end"]
                    cur[2] += s["dur"]
            elif cur is not None:
                spans.append(cur)
                cur = None
        if cur is not None:
            spans.append(cur)

        buckets = [0.0] * 10
        for s in segs:
            if any((t["dim"] + "." + t["tag"]) in GROUPS[g]
                   for t in (s.get("tags") or [])):
                i = min(9, int(s["start"] / total * 10)) if total else 0
                buckets[i] += s["dur"]
        active = sum(1 for b in buckets if b > 0)

        top = [{"key": k, "name": names.get(k, k), "n": n}
               for k, n in st["tags"].most_common(6)]
        out["groups"][g] = {
            "name": LABEL[g],
            "sec": round(st["sec"], 1),
            "pct": round(st["sec"] / total * 100, 1) if total else 0,
            "segments": st["n"],
            "spans": len(spans),
            "avg_span_sec": round(sum(x[2] for x in spans) / len(spans), 1) if spans else 0,
            "max_span_sec": round(max((x[2] for x in spans), default=0), 1),
            "coverage_decile": active,
            "per_min": round(st["n"] / (total / 60), 2) if total else 0,
            "top": top,
        }

    out["other"] = {"sec": round(other["sec"], 1),
                    "pct": round(other["sec"] / total * 100, 1) if total else 0,
                    "note": "未归入三类的话（寒暄、答疑、转场等）"}

    gaps = []
    prev_end = None
    for s in segs:
        if any((t["dim"] + "." + t["tag"]) in GROUPS["逼单"]
               for t in (s.get("tags") or [])):
            if prev_end is not None:
                gaps.append({"start": round(prev_end), "sec": round(s["start"] - prev_end, 1)})
            prev_end = s["end"]
    if prev_end is not None:
        gaps.append({"start": round(prev_end), "sec": round(total - prev_end, 1)})
    if gaps:
        gaps_sorted = sorted(gaps, key=lambda x: -x["sec"])
        out["push_gap"] = {
            "max_sec": gaps_sorted[0]["sec"],
            "max_at": gaps_sorted[0]["start"],
            "over_60s": sum(1 for g in gaps if g["sec"] > 60),
            "n": len(gaps),
            "worst": gaps_sorted[:5],
            "note": "整场中连续没有促单的最长时间；超过 60 秒的空窗观众可能错过下单时机",
        }

    aud = audience(d, segs)
    if aud:
        out["audience"] = aud

    (d / "talk.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    print("完成 -> talk.json", flush=True)
    for g, v in out["groups"].items():
        print("  %-6s %5.1f%% (%s)  段数%d 次/分%.2f  单次均长%.0fs  覆盖%d/10段"
              % (v["name"], v["pct"], "%.0f分" % (v["sec"] / 60), v["segments"],
                 v["per_min"], v["avg_span_sec"], v["coverage_decile"]), flush=True)
    print("  其他 %5.1f%%" % out["other"]["pct"], flush=True)
    if "push_gap" in out:
        pg = out["push_gap"]
        print("  最长促单空窗 %.0f 秒（在 %s），超 60 秒空窗 %d 次" % (
            pg["max_sec"], "%d:%02d" % (pg["max_at"] // 60, pg["max_at"] % 60),
            pg["over_60s"]), flush=True)


if __name__ == "__main__":
    import argparse as _ap
    _a = _ap.ArgumentParser(description="话术结构量化：三类话术的时长占比与分布均衡度")
    _a.add_argument("recdir", help="录制目录")
    _ns = _a.parse_args()
    _d = pathlib.Path(_ns.recdir).expanduser()
    if not _d.is_dir():
        raise SystemExit("不是一个目录：%s" % _d)
    main(str(_d))
