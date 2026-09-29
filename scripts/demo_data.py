#!/usr/bin/env python3
"""生成演示数据：几个虚构直播间、若干场虚构场次，跑完规则层，用来试用看板和截图。

用法:
  python scripts/demo_data.py            # 生成并分析（约 1 分钟）
  python scripts/demo_data.py --clean    # 删掉演示数据，并从 config/rooms.json 里移除演示直播间
然后：python scripts/dashboard.py  → 浏览器打开 http://127.0.0.1:8787
"""
import argparse, datetime, json, pathlib, random, shutil, subprocess, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "douyin"
ROOMS = ROOT / "config" / "rooms.json"
PY = sys.executable

DEMO = {
    "900000000001": {"name": "示例直播间 A · 智能投影", "group": "own", "category": "智能投影", "kind": "proj", "skill": 0.35},
    "900000000002": {"name": "示例直播间 B · 智能投影", "group": "benchmark", "category": "智能投影", "kind": "proj", "skill": 0.8},
    "900000000003": {"name": "示例直播间 C · 厨房电器", "group": "benchmark", "category": "厨房小家电", "kind": "cook", "skill": 0.6},
}

PRODUCTS = {
    "proj": {
        "name": "云幕 X1 投影仪", "price": 2999, "orig": 3999, "gift": "幕布和支架",
        "hook": ["欢迎刚进来的家人们，今天投影仪直接给到年度最低价。", "新进来的朋友点个关注，今晚福利一波接一波。"],
        "pain": ["很多家人买投影最怕白天看不清，画面发灰对不对？", "是不是担心买回去对焦麻烦、画面歪歪扭扭的？"],
        "param": ["亮度做到两千四百流明，白天拉上窗帘就能看。", "分辨率是真 4K，功率一百八十瓦，噪音控制在二十五分贝以内。",
                  "自动对焦加自动梯形校正，放下就是正的。"],
        "demo": ["大家看镜头，我把灯全打开，画面依然很通透。", "我现在把它斜着放，你看，一秒钟自动校正回来了。"],
        "compare": ["相比上一代，亮度提升了百分之四十。", "跟同价位的比，它多了一颗独立的画质芯片，算一下每天不到一块钱。"],
    },
    "cook": {
        "name": "暖厨 IH 电饭煲", "price": 399, "orig": 599, "gift": "蒸笼和饭勺",
        "hook": ["欢迎新来的宝宝们，今天电饭煲福利价只有直播间有。", "刚进来的家人点点关注，马上开福利。"],
        "pain": ["是不是经常煮饭底下糊、上面夹生？", "很多家人担心涂层掉了不健康对不对？"],
        "param": ["四升大容量，三到八口人都够用。", "一千二百瓦 IH 电磁加热，内胆是三毫米厚釜。", "陶瓷涂层，不粘还好清洗。"],
        "demo": ["大家看，我现在把饭盛出来，粒粒分明，锅底一点都不粘。", "我拿勺子刮一下内胆，你看涂层完全没问题。"],
        "compare": ["相比普通底盘加热，IH 是整个内胆一起受热。", "算一下，一天不到三毛钱，用五年都没问题。"],
    },
}
COMMON = {
    "anchor": ["日常价{orig}，专柜价也是{orig}。", "平时卖{orig}，今天不要{orig}。"],
    "price": ["今天直播间到手价只要{price}！", "拍一号链接，到手{price}，还送{gift}。"],
    "urge": ["最后二十单了，拍完就恢复原价。", "库存不多了，喜欢的抓紧去拍。", "三二一，上链接！"],
    "cmd": ["想要的家人扣个1。", "有问题的打在评论区，我一个个回复。", "扣个想要，我看看有多少人。"],
    "ask": ["大家说是不是？", "这个价格划不划算，对不对？", "听懂了吗家人们？"],
    "deal": ["恭喜刚刚拍下的家人，已经给你备注优先发货。", "我看后台又有家人拍了，感谢支持。"],
    "filler": ["好，我们继续。", "然后就是这个。", "嗯，稍等一下。", "这个也还可以。"],
    "qa": ["有家人问保修多久，全国联保两年。", "有朋友问什么时候发货，今天拍四十八小时内发。"],
}
DANMU = {
    "buy": ["多少钱", "链接在哪", "拍了", "已拍", "怎么买", "还有吗", "几号链接"],
    "question": ["保修多久？", "有没有白色的？", "能投多大？", "什么时候发货？", "声音大吗", "适合卧室吗"],
    "praise": ["不错", "支持主播", "看着挺好", "喜欢", "值"],
    "doubt": ["有点贵", "真的假的", "别家更便宜"],
    "cmd": ["1", "1", "想要", "1111"],
}
SURNAME = "王李张刘陈杨黄赵吴周徐孙马朱胡郭何林罗高"


def say(kind, key, rnd):
    p = PRODUCTS[kind]
    bank = p[key] if isinstance(p.get(key), list) else COMMON[key]
    return rnd.choice(bank).format(**p)


def strip_punct(s):
    return "".join(ch for ch in s if ch not in "，。！？、：；,.!? ")


def session(rid, room, day, hh, minutes, rnd):
    """一场：逐句按「开场 → 痛点 → 参数 → 演示 → 对比 → 锚价 → 报价 → 促单」循环，
    句间穿插互动；skill 低的直播间更多填充句、更长停顿。
    """
    sk, kind = room["skill"], room["kind"]
    start = datetime.datetime.combine(day, datetime.time(hh, rnd.randint(0, 20), rnd.randint(0, 59)))
    slot = start.strftime("%Y%m%d_%H%M%S")
    d = DATA / rid / slot
    if d.exists():
        shutil.rmtree(d)
    (d / "frames").mkdir(parents=True)
    dur = minutes * 60
    cycle = ["hook", "pain", "param", "param", "demo", "compare", "anchor", "price", "urge"]
    items, danmu, t, ci = [], [], 1.0, 0
    t0ms = int(start.timestamp() * 1000)
    online = 30 + int(sk * 120)
    while t < dur - 10:
        key = cycle[ci % len(cycle)]
        ci += 1
        if rnd.random() > sk:
            key = rnd.choice(["filler", key])
        lines = [say(kind, key, rnd)]
        if rnd.random() < 0.15 + sk * 0.5:
            lines.append(say(kind, rnd.choice(["cmd", "ask"]), rnd))
        if key in ("price", "urge") and rnd.random() < 0.3 + sk * 0.5:
            lines.append(say(kind, "deal", rnd))
        if rnd.random() < sk * 0.3:
            lines.append(say(kind, "qa", rnd))
        punct = "".join(lines)
        text = strip_punct(punct)
        seg = max(2.0, len(text) / rnd.uniform(5.0, 6.0))
        items.append({"start": round(t, 2), "end": round(t + seg, 2), "text": text, "punct": punct})
        n = rnd.randint(0, 2) + (2 if key in ("price", "urge") else 0) + (2 if "扣" in punct else 0)
        for _ in range(int(n * (0.4 + sk))):
            cat = rnd.choices(list(DANMU), weights=[3, 3, 2, 1, 2 if "扣" in punct else 0.3])[0]
            ts = t0ms + int((t + seg + rnd.uniform(0, 25)) * 1000)
            danmu.append({"ts": ts, "nick": rnd.choice(SURNAME) + "*****", "text": rnd.choice(DANMU[cat]),
                          "role": "", "lv": rnd.randint(1, 40)})
        if rnd.random() < 0.5:
            danmu.append({"ts": t0ms + int(t * 1000), "nick": "", "role": "",
                          "text": "%s***** 来了" % rnd.choice(SURNAME)})
        t += seg + rnd.uniform(0.3, 1.5) + (rnd.uniform(15, 70) if rnd.random() < (1 - sk) * 0.06 else 0)
    danmu.sort(key=lambda x: x["ts"])

    stats, o = [], online
    for s in range(1, dur, 30):
        o = max(5, int(o + rnd.gauss(0, 4) + (3 if any(it["start"] <= s < it["end"] and "到手" in it["punct"]
                                                      for it in items) else 0)))
        stats.append({"t": float(s), "online": o, "likes": None})

    speech = sum(it["end"] - it["start"] for it in items)
    chars = sum(len(it["text"]) for it in items)
    meta = {"platform": "douyin", "url": "", "room_id": rid, "started_at": start.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_sec": dur, "stop_reason": "演示数据", "audio_bytes": 0, "danmu_count": len(danmu),
            "danmu_capture": 2, "chat_opened": 0, "frames": 0, "demo": True}
    info = {"platform": "douyin", "url": "", "room_id": rid, "title": room["name"],
            "started_at": meta["started_at"], "width": 480, "height": 852}
    tr = {"engine": "nano", "model": "demo", "audio_sec": dur, "transcribe_sec": 0, "segments": len(items),
          "chars": chars, "speech_sec": round(speech, 1), "silence_ratio": round(1 - speech / dur, 3),
          "chars_per_min": round(chars / (speech / 60), 1), "punct_model": "demo", "term_fixes": 0,
          "items": items}
    meta["frames"] = frames(d, room, dur, rnd)
    w = lambda name, obj: (d / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "audio.webm").write_bytes(b"demo placeholder, no audio")
    w("meta.json", meta)
    w("info.json", info)
    w("transcript.json", tr)
    (d / "danmu.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in danmu), encoding="utf-8")
    (d / "stats.jsonl").write_text("".join(json.dumps(x) + "\n" for x in stats), encoding="utf-8")
    return d


def font(size):
    from PIL import ImageFont
    for f in ("C:/Windows/Fonts/msyh.ttc", "/System/Library/Fonts/PingFang.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"):
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            pass
    return ImageFont.load_default()


def frames(d, room, dur, rnd):
    """示意画面：纯色渐变背景 + 抽象的人像和商品色块，每分钟一张。"""
    from PIL import Image, ImageDraw
    hue = {"proj": ((34, 48, 92), (86, 110, 190)), "cook": ((96, 52, 28), (214, 150, 92))}[room["kind"]]
    f1, f2 = font(26), font(18)
    n = 0
    for s in range(1, dur, 60):
        im = Image.new("RGB", (480, 852))
        dr = ImageDraw.Draw(im)
        for y in range(852):
            k = y / 851
            dr.line([(0, y), (480, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(*hue)))
        x = 240 + rnd.randint(-30, 30)
        dr.ellipse([x - 70, 250, x + 70, 390], fill=(236, 214, 196))
        dr.rounded_rectangle([x - 130, 400, x + 130, 760], 60, fill=(245, 245, 250))
        if rnd.random() < 0.3 + room["skill"] * 0.4:
            dr.rounded_rectangle([x + 40, 470, x + 190, 580], 14, fill=(40, 40, 48))
        dr.rounded_rectangle([20, 20, 300, 64], 22, fill=(0, 0, 0, 90))
        dr.text((36, 28), room["name"][:12], font=f2, fill=(255, 255, 255))
        dr.text((20, 790), "DEMO · 虚构画面", font=f1, fill=(255, 255, 255))
        im.save(d / "frames" / ("%06d.jpg" % s), quality=70)
        n += 1
    return n


def register(clean=False):
    cfg = json.loads(ROOMS.read_text(encoding="utf-8")) if ROOMS.exists() else {"rooms": {}}
    rooms = cfg.setdefault("rooms", {})
    for rid, r in DEMO.items():
        if clean:
            rooms.pop(rid, None)
        else:
            rooms[rid] = {"name": r["name"], "group": r["group"], "category": r["category"],
                          "url": "", "demo": True}
    if clean:
        if cfg.get("pair") == ["900000000001", "900000000002"]:
            cfg.pop("pair")
    else:
        cfg.setdefault("pair", ["900000000001", "900000000002"])
    ROOMS.parent.mkdir(parents=True, exist_ok=True)
    ROOMS.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def register_anchors(made, clean=False):
    """虚构主播排班：每间两位主播按天轮换，写进 config/anchors.json（主播分析页用）。"""
    f = ROOT / "config" / "anchors.json"
    cfg = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    by = cfg.setdefault("by_room", {})
    for rid in DEMO:
        by.pop(rid, None)
    if not clean:
        names = {"900000000001": ["主播甲", "主播乙"], "900000000002": ["主播丙", "主播丁"],
                 "900000000003": ["主播戊", "主播己"]}
        for d in made:
            rid, day = d.parent.name, "%s-%s-%s" % (d.name[:4], d.name[4:6], d.name[6:8])
            e = by.setdefault(rid, {"name": DEMO[rid]["name"], "source": "demo", "anchors": {}})
            who = names[rid][int(d.name[6:8]) % 2]
            a = e["anchors"].setdefault(who, {"dates": [], "shifts": [], "n": 0})
            if day not in a["dates"]:
                a["dates"].append(day)
                a["n"] += 1
    f.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="生成虚构的演示数据")
    ap.add_argument("--clean", action="store_true", help="删除演示数据")
    ap.add_argument("--days", type=int, default=6, help="生成最近几天（默认 6）")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    if a.clean:
        for rid in DEMO:
            shutil.rmtree(DATA / rid, ignore_errors=True)
        register(clean=True)
        register_anchors([], clean=True)
        print("演示数据已删除")
        return
    register()
    rnd = random.Random(a.seed)
    today = datetime.date.today()
    made = []
    for i in range(a.days, 0, -1):
        day = today - datetime.timedelta(days=i)
        for rid, room in DEMO.items():
            if rnd.random() < 0.8:
                made.append(session(rid, room, day, rnd.choice([10, 14, 19, 20]), rnd.randint(40, 70), rnd))
    register_anchors(made)
    print("生成 %d 场，开始跑规则层 ..." % len(made), flush=True)
    for d in made:
        for step in ("analyze.py", "talk_metrics.py", "commerce.py", "report.py"):
            if not (ROOT / "scripts" / step).exists():
                continue
            r = subprocess.run([PY, str(ROOT / "scripts" / step), str(d)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0:
                print("  %s/%s %s 失败：%s" % (d.parent.name, d.name, step,
                                            ((r.stderr or r.stdout).strip().splitlines() or [""])[-1][:160]))
                break
        else:
            print("  %s %s" % (DEMO[d.parent.name]["name"], d.name), flush=True)
    print("\n完成。启动看板：python scripts/dashboard.py，然后打开 http://127.0.0.1:8787")


if __name__ == "__main__":
    main()
