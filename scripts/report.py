#!/usr/bin/env python3
"""生成单场诊断报告 (report.html)

用法:
  python scripts/report.py <录制目录>
"""
import argparse, html, json, pathlib, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
TAXO = ROOT / "config" / "taxonomy.json"
ROOMS = ROOT / "config" / "rooms.json"


def rj(p, d=None):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return d


def bench_avg(category, fname, pick):
    """同品类标杆在 fname（talk/visual）里指标 pick 的均值。"""
    cfg = (rj(ROOMS) or {}).get("rooms", {})
    vals = []
    for dpath in (ROOT / "data").glob("*/*/*/" + fname):
        rid = dpath.parent.parent.name
        reg = cfg.get(rid, {})
        if reg.get("group") != "benchmark" or reg.get("category") != category:
            continue
        x = rj(dpath)
        if not x:
            continue
        got = pick(x)
        if got:
            vals.append(got)
    if not vals:
        return None
    out = {}
    for k in vals[0].keys():
        xs = [v[k] for v in vals if v.get(k) is not None]
        out[k] = round(sum(xs) / len(xs), 1) if xs else None
    out["_n"] = len(vals)
    return out

CSS = """
:root{--bg:#f7f8fa;--card:#fff;--fg:#1a1d23;--dim:#6b7280;--line:#e5e7eb;
--acc:#2563eb;--ok:#059669;--warn:#d97706;--bad:#dc2626}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){
--bg:#0f1115;--card:#171a21;--fg:#e8eaed;--dim:#9aa0a6;--line:#2a2e37;
--acc:#60a5fa;--ok:#34d399;--warn:#fbbf24;--bad:#f87171}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);padding:24px 16px;
font:14px/1.7 -apple-system,Segoe UI,Microsoft YaHei,sans-serif}
.wrap{max-width:1000px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:32px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.sub{color:var(--dim);font-size:13px;margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .k{color:var(--dim);font-size:12px}
.card .v{font-size:22px;font-weight:600;margin-top:2px;letter-spacing:-.5px}
.card .v small{font-size:13px;font-weight:400;color:var(--dim)}
.bar{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.row{display:flex;align-items:center;gap:10px;margin:7px 0}
.row .lb{width:88px;font-size:13px;color:var(--dim);flex:none;text-align:right}
.row .tr{flex:1;background:var(--line);border-radius:4px;height:16px;overflow:hidden}
.row .fl{height:100%;border-radius:4px}
.row .nm{width:52px;font-size:12px;color:var(--dim);flex:none}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:9px 11px;text-align:left;border-bottom:1px solid var(--line);
font-size:13px;vertical-align:top}
th{background:rgba(127,127,127,.06);font-weight:600;color:var(--dim);font-size:12px}
tr:last-child td{border-bottom:none}
.t{color:var(--acc);font-variant-numeric:tabular-nums;white-space:nowrap;font-size:12px}
.tag{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;
background:rgba(37,99,235,.12);color:var(--acc);margin:1px 3px 1px 0;white-space:nowrap}
.tag.risk{background:rgba(220,38,38,.14);color:var(--bad)}
.tag.p{background:rgba(5,150,105,.13);color:var(--ok)}
.tag.s{background:rgba(217,119,6,.14);color:var(--warn)}
.miss{color:var(--dim);text-decoration:line-through;opacity:.6}
.alert{background:rgba(220,38,38,.08);border:1px solid rgba(220,38,38,.3);
border-radius:10px;padding:14px;margin:10px 0}
.alert b{color:var(--bad)}
.note{color:var(--dim);font-size:12.5px;margin:8px 0 0}
.q{color:var(--dim)}
@media(max-width:600px){body{padding:16px 12px}.row .lb{width:62px}}
"""

DIMCLS = {"product": "p", "sales": "s", "interact": "", "risk": "risk"}


def esc(s):
    return html.escape(str(s))


def mmss(s):
    return "%d:%02d" % (int(s) // 60, int(s) % 60)


def main():
    ap = argparse.ArgumentParser(description="生成诊断报告")
    ap.add_argument("recdir")
    a = ap.parse_args()
    d = pathlib.Path(a.recdir).expanduser()
    an = json.loads((d / "analysis.json").read_text(encoding="utf-8"))
    taxo = json.loads(TAXO.read_text(encoding="utf-8"))
    dims = taxo["dims"]
    m = an["metrics"]
    tc = an["tag_count"]
    talk = rj(d / "talk.json")
    vis = rj(d / "visual.json")
    rounds = rj(d / "rounds.json") or {}
    reg = (rj(ROOMS) or {}).get("rooms", {}).get(an["room"], {})
    cat = reg.get("category", "")
    b_talk = bench_avg(cat, "talk.json",
        lambda x: {k: x["groups"][k]["pct"] for k in ("塑品", "互动", "逼单")
                   if k in x.get("groups", {})}) if cat else None
    b_vis = bench_avg(cat, "visual.json",
        lambda x: {kk: (v.get("value", v.get("pct")))
                   for kk, v in (x.get("derived") or {}).items()}) if cat else None

    H = []
    H.append("<!doctype html><html lang=zh-CN><meta charset=utf-8>")
    H.append("<meta name=viewport content='width=device-width,initial-scale=1'>")
    H.append("<title>直播话术诊断</title><style>" + CSS + "</style><div class=wrap>")
    H.append("<h1>直播话术诊断报告</h1>")
    H.append("<div class=sub>直播间 %s ・ %s ・ 时长 %s ・ 转写 %s/%s ・ 标签体系 v%d</div>"
             % (esc(an["room"]), esc(an["started_at"]), mmss(m["时长_秒"]),
                esc(an["asr"]["engine"]), esc(an["asr"]["model"]),
                an["taxonomy_version"]))

    cards = [
        ("语速", "%s <small>字/分</small>" % m["语速_字每分"]),
        ("行动指令密度", "%s <small>次/分</small>" % m["行动指令_次每分"]),
        ("互动发起密度", "%s <small>次/分</small>" % m["互动发起_次每分"]),
        ("最长无逼单", "%s <small>秒</small>" % m["最长无逼单间隔_秒"]),
        ("弹幕密度", "%s <small>条/分</small>" % m["弹幕_条每分"]),
        ("购买意向弹幕", "%s <small>条</small>" % m["购买意向弹幕"]),
    ]
    H.append("<h2>关键指标</h2><div class=cards>")
    for k, v in cards:
        H.append("<div class=card><div class=k>%s</div><div class=v>%s</div></div>" % (k, v))
    H.append("</div>")

    if talk and talk.get("groups"):
        g = talk["groups"]
        H.append("<h2>话术结构 <span class=q>（时长占比 · 蓝塑品 / 橙逼单 / 绿互动）</span></h2>")
        rows = [("塑品", "#2563eb", g.get("塑品", {})),
                ("逼单", "#d97706", g.get("逼单", {})),
                ("互动", "#059669", g.get("互动", {}))]
        bt = (b_talk or {})
        H.append("<div class=bar>")
        for name, color, x in rows:
            pct = x.get("pct", 0)
            extra = ""
            if bt.get(name) is not None:
                diff = round(pct - bt[name], 1)
                extra = (' <span style="color:var(--dim);font-size:12px">标杆均值 %s%%（%s%.1f%%）</span>'
                         % (bt[name], "+" if diff >= 0 else "", diff))
            H.append('<div class=row><div class=lb>%s</div><div class=tr>'
                     '<div class=fl style="width:%s%%;background:%s"></div></div>'
                     '<div class=nm>%s%%</div></div>%s'
                     % (x.get("name", name), pct, color, pct, extra))
        H.append("</div>")
        H.append("<div class=note>塑品 %.0f 分钟 / 互动 %.0f 分钟 / 逼单 %.0f 分钟。%s</div>"
                 % (g["塑品"]["sec"] / 60, g["互动"]["sec"] / 60, g["逼单"]["sec"] / 60,
                    "。".join("%s常用：%s" % (x.get("name"), "、".join(
                        "%s×%d" % (t["name"], t["n"]) for t in x.get("top", [])[:3]))
                        for x in (g["塑品"], g["逼单"], g["互动"]) if x)))
        pg = talk.get("push_gap")
        if pg:
            tone = "促单密度很好" if pg.get("over_60s", 0) == 0 else "存在较长无人促单的时段"
            H.append("<div class=note>最长促单空窗 <b>%s 秒</b>（出现在 %s），"
                     "超 60 秒空窗 %s 次 —— %s</div>"
                     % (round(pg["max_sec"]), mmss(pg.get("max_at", 0)),
                        pg.get("over_60s", 0), tone))
        elif m.get("最长无逼单间隔_秒") is not None:
            H.append("<div class=note>最长无逼单间隔 %s 秒</div>"
                     % m["最长无逼单间隔_秒"])

    H.append("<h2>能力维度覆盖</h2><div class=bar>")
    for dk, dv in dims.items():
        total = len(dv["tags"])
        got = sum(1 for t in dv["tags"] if tc.get(dk + "." + t))
        pct = got * 100 // total if total else 0
        color = "var(--bad)" if (dk == "risk" and got) else "var(--acc)"
        H.append("<div class=row><div class=lb>%s</div><div class=tr>"
                 "<div class=fl style='width:%d%%;background:%s'></div></div>"
                 "<div class=nm>%d/%d</div></div>"
                 % (dv["name"], pct, color, got, total))
    H.append("</div>")

    H.append("<h2>用了什么・缺了什么</h2><table><tr><th width=80>维度</th>"
             "<th>已使用</th><th>未出现</th></tr>")
    for dk, dv in dims.items():
        if dk == "risk":
            continue
        used, miss = [], []
        for tk, tv in dv["tags"].items():
            n = tc.get(dk + "." + tk, 0)
            if n:
                used.append("<span class='tag %s'>%s ×%d</span>"
                            % (DIMCLS[dk], tv["name"], n))
            else:
                miss.append("<span class=miss>%s</span>" % tv["name"])
        H.append("<tr><td><b>%s</b></td><td>%s</td><td>%s</td></tr>"
                 % (dv["name"], "".join(used) or "<span class=q>无</span>",
                    "、".join(miss) or "<span class=q>全部覆盖</span>"))
    H.append("</table>")
    H.append("<div class=note>紧迫感手法覆盖 %s（稀缺/限时/从众/损失厌恶）・"
             "价格手法覆盖 %s（锚定/堆叠/算账）</div>"
             % (m["紧迫感手法_覆盖"], m["价格手法_覆盖"]))

    risk_keys = [k for k in tc if k.startswith("risk.")]
    risk_danmu = [r for r in an["danmu"] if r.get("risk")]
    if risk_keys or risk_danmu:
        H.append("<h2>违规风险预警</h2>")
        for r in risk_danmu:
            nms = "、".join(dims["risk"]["tags"][t]["name"] for t in r["risk"])
            H.append("<div class=alert><b>弹幕・%s</b>"
                     "<div class=note><span class=t>%s</span> %s：%s</div></div>"
                     % (esc(nms), mmss(r.get("rel", 0)),
                        esc(r.get("speaker", "")), esc(r.get("text", ""))))
        for k in risk_keys:
            tk = k.split(".")[1]
            nm = dims["risk"]["tags"][tk]["name"]
            H.append("<div class=alert><b>%s</b> 命中 %d 次" % (esc(nm), tc[k]))
            for e in an["evidence"].get(k, []):
                H.append("<div class=note><span class=t>%s</span> %s "
                         "<span class=q>（命中：%s）</span></div>"
                         % (mmss(e["t"]), esc(e["text"]), esc("、".join(e["hit"]))))
            H.append("</div>")

    if vis and vis.get("derived"):
        dv = vis["derived"]
        bv = (b_vis or {})
        H.append("<h2>视觉形象画像 <span class=q>（抽 %s 帧逐帧标注）</span></h2>"
                 % vis.get("n_frames"))
        H.append("<table><tr><th>指标</th><th width=110>本场</th>"
                 "<th width=110>标杆均值</th><th>说明</th></tr>")
        for k, x in dv.items():
            val = x.get("value", x.get("pct"))
            if val is None:
                vs = str(x.get("main", "—"))
                if x.get("pct") is not None:
                    vs += "（%s%%）" % x["pct"]
            else:
                vs = str(val) + ("%" if x.get("pct") is not None else "")
            bv_s = "—"
            if bv.get(k) is not None:
                bv_s = str(bv[k]) + ("%" if x.get("pct") is not None else "")
            H.append("<tr><td><b>%s</b></td><td>%s</td><td class=q>%s</td>"
                     "<td class=q>%s</td></tr>"
                     % (esc(k), esc(vs), esc(bv_s),
                        esc(x.get("desc") or x.get("note") or x.get("scale") or "")))
        H.append("</table>")
        props = (vis.get("props") or [])[:6]
        if props:
            H.append("<div class=note>道具出现率：" +
                     "、".join("%s %s%%" % (esc(x["v"]), x["pct"]) for x in props) + "</div>")
        oc = (vis.get("outfit_colors") or [])[:3]
        if oc:
            H.append("<div class=note>服装主色：" +
                     "、".join("%s %s%%" % (esc(x["v"]), x["pct"]) for x in oc) + "</div>")
        pf = vis.get("frames") or []
        people = [f for f in pf
                  if isinstance(f.get("outfit"), dict)
                  and f["outfit"].get("type") not in (None, "", "无")]
        if pf and len(people) / len(pf) < 0.6:
            H.append('<div class=alert><b>本场为无人出镜的产品展示型直播</b>'
                     '<div class=note>画面以商品特写为主（有人物帧仅 %d%%），'
                     '妆造/站位类指标天然为空，不代表主播形象问题</div></div>'
                     % round(len(people) / len(pf) * 100))

    if rounds.get("rounds"):
        ras = {}
        for f in d.glob("round_*.json"):
            x = rj(f)
            if x and x.get("_meta") and x["_meta"].get("idx", 0) > 0:
                ras[x["_meta"]["idx"]] = x
        H.append("<h2>讲解轮次 <span class=q>（%d 轮 · %d 轮已深度诊断）</span></h2>"
                 % (rounds.get("n_rounds", len(rounds["rounds"])), len(ras)))
        H.append("<table><tr><th width=40>轮</th><th>商品</th><th width=96>时段</th>"
                 "<th width=110>评分(产/销/互/节)</th><th>缺环节</th>"
                 "<th>最该改的一件事</th></tr>")
        for r in rounds["rounds"]:
            a = ras.get(r["idx"])
            sc = (a or {}).get("score") or {}
            sc_s = ("%s/%s/%s/%s" % (sc.get("product", "—"), sc.get("sales", "—"),
                                     sc.get("interact", "—"), sc.get("pace", "—"))
                    if sc else "—")
            miss = "、".join(((a or {}).get("structure") or {}).get("missing", [])) or "—"
            comment = sc.get("comment", esc(r.get("summary", ""))[:60])
            H.append("<tr><td><b>%d</b></td><td>%s</td><td class=t>%s-%s</td>"
                     "<td>%s</td><td>%s</td><td class=q>%s</td></tr>"
                     % (r["idx"], esc(r.get("product", "")), mmss(r["start"]),
                        mmss(r["end"]), esc(sc_s), esc(miss), comment))
        H.append("</table>")

    cm = rj(d / "commerce.json")
    if cm:
        sm = cm["summary"]
        H.append("<h2>成交信号 <span class=q>（%d 条，其中 %d 条有两个来源互相印证；只有一个来源的标「待确认」）</span></h2>"
                 % (sm["成交信号"], sm["成交确认"]))
        if cm["deals"]:
            H.append("<table><tr><th width=52>时间</th><th width=64>状态</th><th width=110>谁</th>"
                     "<th width=60>链接</th><th width=90>价格</th><th width=110>来源</th><th>原话</th></tr>")
            for x in cm["deals"]:
                pr = ("%s %.0f" % (x.get("price_kind", ""), x["price"])) if x.get("price") else "—"
                H.append("<tr><td class=t>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                         % (mmss(x["t"]), x["status"], esc(x.get("who") or "（没点名）"),
                            ("%s号" % x["link"]) if x.get("link") else "—", pr,
                            "+".join(x["sources"]), esc(" / ".join(x["evidence"][:2]))))
            H.append("</table>")
        else:
            H.append("<div class=note>这场没有抓到成交信号（主播没有口播感谢下单，弹幕里也没人说拍了）。</div>")
        if cm["quote_table"]:
            H.append("<h2>报价台账 <span class=q>（「N 号链接」附近的价格；同链接同口径出现不同价会标 ⚠）</span></h2>"
                     "<table><tr><th width=70>链接</th><th width=50>次数</th><th>报价</th><th width=200>提示</th></tr>")
            for r in cm["quote_table"]:
                pr = "；".join("%s %s" % (k, " / ".join("%.0f" % v for v in vs)) for k, vs in r["prices"].items())
                warn = ("⚠ 同口径不同价：%s" % "；".join("%s %s" % (k, "/".join("%.0f" % v for v in vs))
                                                   for k, vs in r["conflict"].items())) if r["conflict"] else ""
                H.append("<tr><td>%s号链接</td><td class=q>%d</td><td>%s</td><td>%s</td></tr>"
                         % (r["link"], r["n"], esc(pr), esc(warn)))
            H.append("</table>")
        vw = [v for v in cm.get("viewers", []) if v["stage"] != "只发言"][:15]
        if vw:
            H.append("<h2>观众旅程 <span class=q>（按昵称 + 等级串起来；问了什么、口播接没接住、有没有成交信号）</span></h2>"
                     "<table><tr><th width=110>观众</th><th width=50>等级</th><th width=80>阶段</th>"
                     "<th width=90>停留</th><th>提问（✓ 口播接住 / ✗ 没接住）</th></tr>")
            for v in vw:
                qs = "；".join("%s%s" % ("✓" if q["answered"] else "✗", esc(q["text"])) for q in v["questions"][:4])
                H.append("<tr><td>%s</td><td class=q>%s</td><td>%s</td><td class=t>%s–%s</td><td>%s</td></tr>"
                         % (esc(v["nick"]), v["lv"] if v["lv"] is not None else "—",
                            v["stage"] + ("（%s）" % v["deal_status"] if v.get("deal_status") else ""),
                            mmss(v["first"]), mmss(v["last"]), qs or "—"))
            H.append("</table>")
        notes = []
        if cm.get("lulls") is not None:
            lu = cm["lulls"]
            notes.append("冷场（连续 10 分钟以上没有观众发言）%d 段，共 %d 分钟%s"
                         % (len(lu), sm["冷场分钟"], "：" + "、".join("%s 起 %d 分钟" % (mmss(x["start"]), x["sec"] // 60) for x in lu[:5]) if lu else ""))
        else:
            notes.append("这场弹幕采集不完整，不算冷场")
        rp = cm["repeats"]
        notes.append("重复讲解：讲产品的内容里 %.1f%% 在 5 分钟前已经讲过%s"
                     % (rp["repeat_pct"], "（例：%s「%s…」）" % (mmss(rp["segments"][0]["t"]), esc(rp["segments"][0]["sample"][:30])) if rp["segments"] else ""))
        H.append("<h2>冷场与重复</h2><div class=note>%s</div>" % "<br>".join(notes))

    H.append("<h2>话术时间轴</h2><table><tr><th width=52>时间</th><th width=38>秒</th>"
             "<th>话术内容</th><th width=180>标签</th><th width=64>后续弹幕</th></tr>")
    for s in an["segments"]:
        tags = "".join("<span class='tag %s'>%s</span>"
                       % (DIMCLS.get(t["dim"], ""), t["name"]) for t in s["tags"])
        dm = "%d" % s["danmu_after"]
        if s["danmu_buy_after"]:
            dm += " <span class=tag>买%d</span>" % s["danmu_buy_after"]
        H.append("<tr><td class=t>%s</td><td class=q>%.0f</td><td>%s</td>"
                 "<td>%s</td><td class=q>%s</td></tr>"
                 % (mmss(s["start"]), s["dur"], esc((s.get("text_p") or s["text"])[:120]),
                    tags or "<span class=q>—</span>", dm))
    H.append("</table>")

    dl = an["danmu"]
    if dl:
        H.append("<h2>弹幕与互动应答</h2><table><tr><th width=52>时间</th>"
                 "<th width=120>发言人</th><th>内容</th><th width=86>意图</th></tr>")
        for r in dl[:40]:
            it = "".join("<span class=tag>%s</span>" % taxo["danmu_intent"][i]["name"]
                         for i in r.get("intent", []))
            who = esc(r.get("speaker", ""))
            if r.get("reply_to"):
                who += " <span class=q>→@%s</span>" % esc(r["reply_to"])
            H.append("<tr><td class=t>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (mmss(r.get("rel", 0)), who, esc(r.get("text", "")), it))
        H.append("</table>")
        if m.get("回复延迟中位_秒") is not None:
            H.append("<div class=note>中控回复 %d 次，回复延迟中位数 %s 秒</div>"
                     % (m["中控回复数"], m["回复延迟中位_秒"]))

    H.append("<h2>说明</h2><div class=note>"
             "话术标签由关键词规则命中；讲解逻辑/观众洞察/画面道具由模型层语义分析；"
             "视觉形象由每场均匀抽样逐帧标注（枚举标签，可跨场对比）。<br>"
             "标杆均值为同品类已采集标杆场次的平均值，当前样本量有限"
             "（同品类 15 场起才有方向性参考，50 场起可用于考核），仅供对照、不做优劣判定。</div>")
    H.append("</div></html>")

    p = d / "report.html"
    p.write_text("\n".join(H), encoding="utf-8")
    print("报告已生成 -> %s" % p)


if __name__ == "__main__":
    main()
