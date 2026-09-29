#!/usr/bin/env python3
"""分析层：转写 + 弹幕 -> 结构化标注与指标 (analysis.json)

用法:
  python scripts/analyze.py <录制目录>
"""
import argparse, bisect, json, pathlib, re, sys, time
from collections import Counter, defaultdict
from _reading import read_items

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
TAXO = ROOT / "config" / "taxonomy.json"
ROOMS = ROOT / "config" / "rooms.json"

SEG_TARGET = 45.0
SEG_GAP = 2.0
REACT_WIN = 60.0


def segment(items):
    """按语义停顿优先、时长上限兜底切成话术片段。"""
    segs, cur = [], []
    for it in items:
        if cur and (it["start"] - cur[-1]["end"] > SEG_GAP
                    or it["end"] - cur[0]["start"] > SEG_TARGET):
            segs.append(cur); cur = []
        cur.append(it)
    if cur:
        segs.append(cur)
    return segs


def tag_text(text, dims):
    """关键词命中 -> 标签。返回 [(dim, tag, name, [命中词])]"""
    out = []
    for dk, dv in dims.items():
        for tk, tv in dv["tags"].items():
            hits = [w for w in tv["kw"] if w in text]
            if hits:
                out.append({"dim": dk, "dim_name": dv["name"], "tag": tk,
                            "name": tv["name"], "hits": hits})
    return out


def load_danmu(p, quiet=False):
    """读弹幕。坏行要计数并在超阈值时出声 ——
    """
    rows, bad = [], 0
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    bad += 1
    if bad and not quiet:
        print("  !! danmu.jsonl 有 %d 行解析失败已跳过（共 %d 行可用），"
              "指标会偏低" % (bad, len(rows)), flush=True)
    rows.sort(key=lambda r: r.get("ts", 0))
    return rows


def mark_host(danmu, d):
    """给每条弹幕标 host：是不是直播间自己的账号（中控）发的。
    """
    if any("role" in r for r in danmu):
        for r in danmu:
            r["host"] = r.get("role") == "anchor"
        return
    try:
        name = json.loads(ROOMS.read_text(encoding="utf-8"))["rooms"][d.parent.name]["name"]
    except Exception:
        name = ""
    core = re.sub(r"(官方|旗舰店|专卖店|直播间).*$", "", name)
    m = re.match(r"[A-Za-z]+|[\u4e00-\u9fff]{1,2}", core)
    brand = m.group(0) if m else ""
    if not brand:
        for r in danmu:
            r["host"] = False
        return
    mine = lambda r: "*" in r.get("nick", "") and r["nick"].startswith(brand[0])
    other = set()
    for f in d.parent.glob("*/danmu.jsonl"):
        if f.parent == d:
            continue
        for r in load_danmu(f, quiet=True):
            if mine(r):
                other.add((r["nick"], r.get("text", "")))
    for r in danmu:
        t = r.get("text", "")
        r["host"] = bool(mine(r) and (brand in t or t.startswith("@") or "【" in t
                                      or (r["nick"], t) in other))


ANS_BEFORE, ANS_AFTER = 15, 120
RARE_DF = 0.1
_STOP = set("吗呢吧啊呀的了是有没么什怎我你您他她这那个能不可以要还就都也在和与及哈嘛呗啥几多少")
_CN_DIGIT = str.maketrans("零一二三四五六七八九", "0123456789")


def _grams(text):
    out = set()
    for w in re.findall(r"[\u4e00-\u9fff]+", text):
        out.update(w[i:i + 2] for i in range(len(w) - 1) if not set(w[i:i + 2]) & _STOP)
    return out


def _tokens(text):
    """型号、数字：x7max、75、2249。转写会把 7 写成「七」，比对前统一成数字。"""
    return {t for t in re.findall(r"[a-z0-9]{2,}", text.lower()) if re.search(r"\d", t)}


def question_hits(questions, items, segments):
    """逐条判断提问有没有在口播里被接住。返回 (每条是否接住, 基线命中列表)。"""
    if not questions or not items:
        return [], []
    seg_grams = [_grams(s["text"]) for s in segments]
    df = lambda g: sum(1 for x in seg_grams if g in x) / max(len(seg_grams), 1)
    norm = lambda t: t.lower().replace(" ", "").translate(_CN_DIGIT)
    end = items[-1]["end"]

    def hit(q, t):
        txt = "".join(x["text"] for x in items if t - ANS_BEFORE <= x["start"] <= t + ANS_AFTER)
        rare = {g for g in _grams(q["text"]) if df(g) <= RARE_DF}
        return bool(rare & _grams(txt)) or any(k in norm(txt) for k in _tokens(q["text"]))

    real = [hit(q, q["rel"]) for q in questions]
    ctrl = []
    for q in questions:
        for off in (-900, -600, 600, 900):
            t = q["rel"] + off
            if 0 <= t <= end:
                ctrl.append(hit(q, t))
    return real, ctrl


def question_response(questions, items, segments):
    real, ctrl = question_hits(questions, items, segments)
    if not real:
        return None
    return {"n": len(questions), "answered": sum(real),
            "rate": round(sum(real) / len(real) * 100),
            "baseline": round(sum(ctrl) / len(ctrl) * 100) if ctrl else None}


def danmu_intent(text, intents):
    return [k for k, v in intents.items() if any(w in text for w in v["kw"])]


def main():
    ap = argparse.ArgumentParser(description="话术分析")
    ap.add_argument("recdir")
    a = ap.parse_args()
    d = pathlib.Path(a.recdir).expanduser()

    taxo = json.loads(TAXO.read_text(encoding="utf-8"))
    dims, intents = taxo["dims"], taxo["danmu_intent"]
    tr = json.loads((d / "transcript.json").read_text(encoding="utf-8"))
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    danmu = load_danmu(d / "danmu.jsonl")

    items = tr["items"]
    audio_sec = tr["audio_sec"]
    real = meta.get("duration_sec") or 0
    if real and audio_sec > real * 1.5:
        print("  !! 转写时长 %.0fs 与实测 %ds 不符（WAV 头占位值），按实测计算"
              % (audio_sec, real), flush=True)
        audio_sec = float(real)
    try:
        base = time.mktime(time.strptime(meta["started_at"], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        base = min((r["ts"] for r in danmu), default=0) / 1000
    danmu = [r for r in danmu if r.get("nick") or not (
        r.get("text", "").endswith("来了") or r.get("text", "").startswith("欢迎来到直播间"))]
    mark_host(danmu, d)
    for r in danmu:
        r["rel"] = round(max(r["ts"] / 1000 - base, 0), 1)
        r["intent"] = danmu_intent(r.get("text", ""), intents)
        r["risk"] = [tk for tk, tv in dims["risk"]["tags"].items()
                     if any(w in r.get("text", "") for w in tv["kw"])]
        nick = r.get("nick", "")
        m = re.search(r"[：:]\s*@(.+)$", nick)
        if not m and r["host"]:
            m = re.match(r"@(\S+)\s", r.get("text", ""))
        r["reply_to"] = m.group(1).strip() if m else ""
        r["speaker"] = re.sub(r"[：:]\s*@.+$", "", nick).strip()

    aud = [r for r in danmu if not r["host"]]

    segs_raw = segment(items)
    read = read_items(d)
    rbucket = defaultdict(list)
    if read:
        starts = [g[0]["start"] for g in segs_raw]
        for r in read:
            rbucket[max(bisect.bisect_right(starts, (r["start"] + r["end"]) / 2) - 1, 0)].append(r["text"])
    segments = []
    for i, grp in enumerate(segs_raw):
        text = "".join(x["text"] for x in grp)
        text_p = ("".join(rbucket[i]) if rbucket.get(i)
                  else "".join(x.get("punct") or x["text"] for x in grp))
        s0, s1 = grp[0]["start"], grp[-1]["end"]
        tags = tag_text(text, dims)
        after = [r for r in aud if s1 <= r["rel"] < s1 + REACT_WIN]
        segments.append({
            "idx": i, "start": round(s0, 1), "end": round(s1, 1),
            "dur": round(s1 - s0, 1), "chars": len(text), "text": text, "text_p": text_p,
            "lines": grp, "tags": tags,
            "danmu_after": len(after),
            "danmu_buy_after": sum(1 for r in after if "buy" in r["intent"]),
        })

    tag_count = Counter()
    dim_segs = defaultdict(int)
    evidence = defaultdict(list)
    for s in segments:
        for t in s["tags"]:
            key = t["dim"] + "." + t["tag"]
            tag_count[key] += 1
            if len(evidence[key]) < 3:
                evidence[key].append({"t": s["start"], "text": s["text_p"][:70],
                                      "hit": t["hits"][:3]})
        for dk in {t["dim"] for t in s["tags"]}:
            dim_segs[dk] += 1

    speech = tr["speech_sec"] or audio_sec
    mins = speech / 60 if speech else 1

    def used(dim):
        return sum(1 for t in dims[dim]["tags"] if tag_count.get(dim + "." + t))

    urgency = ["scarce", "urgent", "crowd", "loss"]
    pricing = ["anchor", "stack", "calc"]
    cta_segs = [s for s in segments if any(t["tag"] == "cta" for t in s["tags"])]
    gaps = []
    prev = 0.0
    for s in cta_segs:
        gaps.append(round(s["start"] - prev, 1)); prev = s["end"]
    gaps.append(round(audio_sec - prev, 1))

    anchor_q = [r for r in aud if "question" in r["intent"] and not r["reply_to"]]
    qr = question_response(anchor_q, items, segments)
    replies = [r for r in danmu if r["reply_to"]]
    delays = []
    for rep in replies:
        cand = [q for q in aud if q["speaker"].startswith(rep["reply_to"][:3])
                and q["rel"] < rep["rel"]]
        if cand:
            delays.append(round(rep["rel"] - cand[-1]["rel"], 1))

    metrics = {
        "时长_秒": audio_sec,
        "有效说话_秒": tr["speech_sec"],
        "语速_字每分": tr["chars_per_min"],
        "片段数": len(segments),
        "产品力_标签覆盖": "%d/%d" % (used("product"), len(dims["product"]["tags"])),
        "销售力_标签覆盖": "%d/%d" % (used("sales"), len(dims["sales"]["tags"])),
        "互动力_标签覆盖": "%d/%d" % (used("interact"), len(dims["interact"]["tags"])),
        "紧迫感手法_覆盖": "%d/4" % sum(1 for t in urgency if tag_count.get("sales." + t)),
        "价格手法_覆盖": "%d/3" % sum(1 for t in pricing if tag_count.get("sales." + t)),
        "行动指令_次每分": round(tag_count.get("sales.cta", 0) / mins, 1),
        "互动发起_次每分": round(sum(tag_count.get("interact." + t, 0)
                                for t in ("ask", "cmd")) / mins, 1),
        "最长无逼单间隔_秒": max(gaps) if gaps else audio_sec,
        "弹幕总数": len(aud),
        "弹幕_条每分": round(len(aud) / (audio_sec / 60), 1) if audio_sec else 0,
        "购买意向弹幕": sum(1 for r in aud if "buy" in r["intent"]),
        "提问弹幕": len(anchor_q),
        "中控弹幕数": len(danmu) - len(aud),
        "中控回复数": len(replies),
        "回复延迟中位_秒": sorted(delays)[len(delays) // 2] if delays else None,
        "提问口播回应率_%": (qr or {}).get("rate"),
        "提问回应基线_%": (qr or {}).get("baseline"),
        "口播风险话术": sum(v for k, v in tag_count.items() if k.startswith("risk.")),
        "弹幕风险话术": sum(1 for r in danmu if r.get("risk")),
    }

    out = {
        "room": meta.get("room_id"), "url": meta.get("url"),
        "started_at": meta.get("started_at"),
        "taxonomy_version": taxo["version"],
        "asr": {"engine": tr.get("engine"), "model": tr.get("model"),
                "reading": "qwen3-asr" if read else None},
        "metrics": metrics,
        "tag_count": dict(tag_count),
        "evidence": {k: v for k, v in evidence.items()},
        "segments": segments,
        "danmu": danmu,
    }
    p = d / "analysis.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print("分析完成 -> %s\n" % p)
    for k, v in metrics.items():
        print("  %-22s %s" % (k, v))
    print("\n  命中标签 TOP:")
    for k, c in tag_count.most_common(10):
        dk, tk = k.split(".")
        print("    %s/%s  %d 次" % (dims[dk]["name"], dims[dk]["tags"][tk]["name"], c))


if __name__ == "__main__":
    main()
