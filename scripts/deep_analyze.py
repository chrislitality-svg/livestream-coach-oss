#!/usr/bin/env python3
"""深度分析：规则层之上的语义理解层。

用法:
  python scripts/deep_analyze.py <录制目录>
  python scripts/deep_analyze.py <录制目录> --no-visual    # 跳过画面（省钱）
"""
import argparse, base64, json, os, pathlib, re, sys, time
import _llm
import _standard
import _reading
try:
    CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
except Exception:
    CREATE_NO_WINDOW = 0


try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROOMS = ROOT / "config" / "rooms.json"
MIN_SCRIPT_CHARS = 200
TEXT_MODEL = _llm.text_model()
VL_MODEL = _llm.vl_model()
MAX_FRAMES = 9


def client():
    return _llm.client()


def ask_json(cli, model, messages, max_tokens=3000, retries=2):
    """转调 _llm.ask_json —— 全项目唯一一份实现，见那里的说明。
    """
    return _llm.ask_json(cli, model, messages, max_tokens, retries,
                         temperature=0.2)


def mmss(s):
    return "%d:%02d" % (int(s) // 60, int(s) % 60)


SCRIPT_PROMPT = """你是直播话术分析专家。下面是一场直播带货的逐字稿（含时间戳，单位秒）。

请分析并只输出 JSON，不要任何额外说明：
{
 "structure": {
   "opening": "开场是怎么切入的（一句话）",
   "flow": ["讲解推进的主要环节，按顺序，每项一句话，4-8项"],
   "closing": "这段结尾如何收口（一句话）",
   "cycle": "是否构成完整讲解循环（引入-痛点-讲品-演示-价格-逼单-承接），缺了哪几环"
 },
 "logic_chains": [
   {"point":"卖点名", "method":"论证手法：FAB/对比/痛点方案/演示/权威背书/数据/类比/无（只是陈述或口头禅，没有展开论证）",
    "how":"具体怎么论证的，一句话", "quote":"最有代表性的原话（20-40字）", "t":时间戳秒}
 ],
 "core_point": {"what":"贯穿全场的核心卖点", "repeat":重复次数, "judge":"是否聚焦，一句话"},
 "highlights": [{"quote":"值得学的原话", "t":秒, "why":"好在哪，一句话"}],
 "issues": [{"problem":"讲解上的问题", "t":秒, "fix":"具体怎么改"}]
}

要求：
- logic_chains 最多 8 条，highlights、issues 各最多 5 条
- 只讲这份稿子里真实存在的内容

逐字稿：
"""

AUDIENCE_PROMPT = """你是直播运营分析专家。下面是一场直播带货中观众发的弹幕（已剔除中控刷屏）。

请分析并只输出 JSON：
{
 "concerns": [{"topic":"观众关心的产品细节主题", "n":相关弹幕条数,
               "samples":["原始弹幕1","原始弹幕2"], "answered":true或false}],
 "doubts": [{"doubt":"顾虑或质疑点", "sample":"原始弹幕"}],
 "unanswered": [{"question":"没被回应的提问", "sample":"原始弹幕"}],
 "positive": ["观众自发的正面反馈点"],
 "summary": "一句话总结这场观众最关心什么"
}

要求：
- concerns 按关注度排序，最多 8 条，topic 要具体（如"内胆材质是否安全"而不是"产品质量"）
- 只归纳弹幕里真实出现的内容
- answered 判断依据：弹幕里是否有带 @ 的回复对应了这个问题

弹幕：
"""

VISUAL_PROMPT = """这些是同一场直播带货不同时刻的画面截图（按时间顺序）。

请分析并只输出 JSON：
{
 "scene": "直播间场景布置描述（背景、台面、灯光风格）",
 "props": [{"name":"道具/物料名称", "usage":"它在直播里起什么作用、怎么用的"}],
 "display": [{"method":"商品展示手法", "detail":"具体怎么做的"}],
 "anchor": {"look":"主播服化道：着装风格、颜色、妆造，只写看得见的",
            "fit": {"verdict":"一致 / 不搭 / 无法判断", "reason":"指出截图里具体哪些细节"},
            "changes":"这些截图里主播换装几次，看起来是不是同一个人在播",
            "action":"主播的主要动作行为"},
 "screen_elements": ["画面上的信息元素：价格牌、贴片文案、字幕、利益点标签等"],
 "techniques": ["值得借鉴的视觉呈现技巧，具体一点；没有就返回空数组"],
 "issues": ["画面呈现上的问题"]
}

要求：只描述截图里真实可见的内容，不要臆测。props 和 techniques 要具体到能被模仿。"""


def analyze_script(cli, tr, limit_chars=14000):
    items = tr["items"]
    lines, total = [], 0
    for it in items:
        s = "[%s] %s" % (mmss(it["start"]), it.get("punct") or it["text"])
        if total + len(s) > limit_chars:
            break
        lines.append(s)
        total += len(s)
    text = "\n".join(lines)
    if total < MIN_SCRIPT_CHARS:
        print("  话术分析：转写仅 %d 字，不足 %d 字，跳过"
              % (total, MIN_SCRIPT_CHARS), flush=True)
        return None, 0
    print("  话术分析：%d 句 / %d 字 ..." % (len(lines), total), flush=True)
    return ask_json(cli, TEXT_MODEL,
                    _standard.system_message() + [{"role": "user", "content": SCRIPT_PROMPT + text}], 3500)


def analyze_audience(cli, an):
    rows = [r for r in an.get("danmu", []) if not r.get("host") and not r.get("spam")]
    from collections import Counter
    cnt = Counter(r.get("text", "") for r in rows)
    real = [r for r in rows if cnt[r.get("text", "")] < 3 or len(r.get("text", "")) < 6]
    if len(real) < 3:
        print("  观众分析：真人弹幕不足 3 条，跳过", flush=True)
        return None, 0
    lines = []
    for r in real[:220]:
        who = r.get("speaker") or r.get("nick", "")
        at = ("→@" + r["reply_to"]) if r.get("reply_to") else ""
        lines.append("[%s] %s%s: %s" % (mmss(r.get("rel", 0)), who, at, r.get("text", "")))
    print("  观众分析：%d 条真人弹幕 ..." % len(lines), flush=True)
    return ask_json(cli, TEXT_MODEL,
                    [{"role": "user", "content": AUDIENCE_PROMPT + "\n".join(lines)}], 2600)


def analyze_visual(cli, d):
    fs = sorted((d / "frames").glob("*.jpg"))
    if not fs:
        return None, 0
    step = max(1, len(fs) // MAX_FRAMES)
    pick = fs[::step][:MAX_FRAMES]
    content = []
    for f in pick:
        b64 = base64.b64encode(f.read_bytes()).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b64}})
    try:
        r = json.loads(ROOMS.read_text(encoding="utf-8"))["rooms"].get(d.parent.name, {})
    except Exception:
        r = {}
    ctx = "直播间：%s（品类：%s）\n\n" % (r.get("name", d.parent.name), r.get("category") or "未登记")
    content.append({"type": "text", "text": ctx + VISUAL_PROMPT})
    print("  画面分析：%d/%d 张抽样 ..." % (len(pick), len(fs)), flush=True)
    return ask_json(cli, VL_MODEL, _standard.system_message()
                    + [{"role": "user", "content": content}], 2600)


def main():
    ap = argparse.ArgumentParser(description="深度语义分析")
    ap.add_argument("recdir")
    ap.add_argument("--no-visual", action="store_true", help="跳过画面分析")
    ap.add_argument("--force", action="store_true", help="已有结果也重跑")
    a = ap.parse_args()

    d = pathlib.Path(a.recdir).expanduser()
    out = d / "deep.json"
    if out.exists() and not a.force:
        print("已有 deep.json，加 --force 重跑")
        return
    tr = json.loads((d / "transcript.json").read_text(encoding="utf-8"))
    an = json.loads((d / "analysis.json").read_text(encoding="utf-8"))

    cli = client()
    t0, tok = time.time(), 0
    res = {"model": {"text": TEXT_MODEL, "visual": VL_MODEL},
           "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    r, n = analyze_script(cli, {**tr, "items": _reading.reading_items(d)}); tok += n
    if r:
        res["script"] = r
    r, n = analyze_audience(cli, an); tok += n
    if r:
        res["audience"] = r
    if not a.no_visual and VL_MODEL:
        r, n = analyze_visual(cli, d); tok += n
        if r:
            res["visual"] = r

    got = [k for k in ("script", "audience", "visual") if res.get(k)]
    if not got:
        print("三段分析全部失败，不写 deep.json —— 留着让下一轮重试。", flush=True)
        return 1
    want = 2 if a.no_visual else 3
    if len(got) < want:
        print("  注意：只拿到 %s（共 %d 段），缺的部分可加 --force 重跑"
              % ("、".join(got), want), flush=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n完成（%.0f 秒，%d token）-> %s" % (time.time() - t0, tok, out))

    sc = res.get("script", {})
    if sc.get("logic_chains"):
        print("\n讲解逻辑链：")
        for c in sc["logic_chains"][:4]:
            print("  [%s] %s —— %s" % (mmss(c.get("t", 0)), c.get("point"), c.get("method")))
    au = res.get("audience", {})
    if au.get("concerns"):
        print("\n观众关注点：")
        for c in au["concerns"][:4]:
            print("  %s（%d条，%s）" % (c.get("topic"), c.get("n", 0),
                                    "已回应" if c.get("answered") else "未回应"))
    vi = res.get("visual", {})
    if vi.get("props"):
        print("\n画面道具：")
        for x in vi["props"][:4]:
            print("  %s —— %s" % (x.get("name"), str(x.get("usage"))[:44]))


if __name__ == "__main__":
    sys.exit(main() or 0)
