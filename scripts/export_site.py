#!/usr/bin/env python3
"""导出静态站点：把分析平台导出成纯静态文件，放到任意 Web 服务器（如 nginx）下即可访问。

用法:
  python scripts/export_site.py                    # 导出到 out/site
  python scripts/export_site.py --out D:/site       # 指定目录
  python scripts/export_site.py --max-thumbs 40    # 每场最多留几张缩略图
"""
import argparse, json, os, pathlib, re, shutil, sys, time
try:
    CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
except Exception:
    CREATE_NO_WINDOW = 0

from collections import Counter
from html import escape as html_escape
from urllib.parse import quote

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import dashboard as DASH

TAXO = ROOT / "config" / "taxonomy.json"


def rj(p, d=None):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return d


def tag_names():
    t = rj(TAXO) or {}
    out = {}
    for dk, dv in (t.get("dims") or {}).items():
        for tk, tv in (dv.get("tags") or {}).items():
            out[dk + "." + tk] = tv.get("name", tk)
    return out


def dims_order():
    t = rj(TAXO) or {}
    return [{"key": dk, "name": dv["name"], "tags": list((dv.get("tags") or {}).keys())}
            for dk, dv in (t.get("dims") or {}).items()]


def make_thumb(src, dst, width=420, quality=62):
    """压到约 12KB。VPS 只剩 2.6G，原图 55KB×335 张/场就塞不下。"""
    try:
        from PIL import Image
    except ImportError:
        shutil.copy2(src, dst)
        return
    try:
        im = Image.open(src).convert("RGB")
        w, h = im.size
        if w > width:
            im = im.resize((width, int(h * width / w)), Image.LANCZOS)
        im.save(dst, "JPEG", quality=quality, optimize=True)
    except Exception:
        shutil.copy2(src, dst)


def ensure_thumb(rel, out, sec):
    """确保场次 thumbs 里存在指定秒的帧（案例帧可能不在抽稀集合里）。"""
    tdir = out / rel / "thumbs"
    tdir.mkdir(parents=True, exist_ok=True)
    dst = tdir / ("%06d.jpg" % int(sec))
    if dst.exists():
        return
    src = ROOT / rel / "frames" / ("%06d.jpg" % int(sec))
    if src.exists():
        make_thumb(src, dst)


def collect_example_secs(ex):
    """从 examples/prop_examples 结构里收集 (场次相对路径, 秒)。"""
    secs = set()
    def walk(x):
        if isinstance(x, dict):
            if "path" in x and "sec" in x:
                secs.add((x["path"], int(x["sec"])))
            else:
                for v in x.values():
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(ex)
    return secs


def export_sessions(out, max_thumbs):
    """每场：落地页 + 报告 + 缩略图。返回给 data.json 用的场次索引。"""
    ss = DASH.scan()
    tree_raw, pend = DASH.tree_data(ss)
    sessions = {}
    for s in ss:
        d = s["dir"]
        rel = s["path"]
        tgt = out / rel
        tgt.mkdir(parents=True, exist_ok=True)
        rp = d / "report.html"
        has_report = rp.exists()
        if has_report:
            shutil.copy2(rp, tgt / "report.html")
        fdir = d / "frames"
        secs = []
        if fdir.exists():
            fs = sorted(fdir.glob("*.jpg"), key=lambda f: int(f.stem))
            secs = [int(f.stem) for f in fs]
            pick = fs
            if len(fs) > max_thumbs:
                step = len(fs) / max_thumbs
                pick = [fs[min(int(i * step), len(fs) - 1)] for i in range(max_thumbs)]
                secs = [int(f.stem) for f in pick]
            (tgt / "thumbs").mkdir(exist_ok=True)
            for f in pick:
                make_thumb(f, tgt / "thumbs" / f.name)
        det = DASH.session_detail(s)
        det.pop("dir", None)
        vm = det.get("visual_metrics") or {}
        for rel0, sec0 in sorted(collect_example_secs(
                {"e": vm.get("examples"), "p": vm.get("prop_examples")})):
            ensure_thumb(rel, out, sec0)
        sessions[rel] = {
            "path": rel, "room": s["room"], "name": s["name"], "group": s["group"],
            "category": s["category"], "started": s["started"], "elapsed": s["elapsed"],
            "danmu": s["danmu"], "chars": s["chars"], "analyzed": s["analyzed"],
            "has_report": has_report, "frame_secs": secs,
            "metrics": det.get("metrics", {}), "tag_count": rj(d / "analysis.json", {}).get("tag_count", {}),
            "evidence": {k: v for k, v in (rj(d / "analysis.json", {}) or {}).get("evidence", {}).items()},
            "dims": det.get("dims", []), "top": det.get("top", []),
            "flow": det.get("flow", []), "danmu_real": det.get("danmu_real", []),
            "danmu_spam": det.get("danmu_spam", []), "spam_total": det.get("spam_total", 0),
            "risk": det.get("risk", []), "deep": det.get("deep"),
            "rounds": det.get("rounds", []),
            "visual_metrics": DASH._vm_with_examples(d),
            "talk": rj(d / "talk.json"),
            "round_analysis": det.get("round_analysis", []),
            "transcript_chars": det.get("transcript_chars", 0),
        }
        up = "../" * len(rel.strip("/").split("/"))
        dest = "%sindex.html#/s?p=%s" % (up, quote(rel, safe=""))
        (tgt / "index.html").write_text(
            '<!doctype html><meta charset=utf-8><title>%s</title>'
            '<meta http-equiv=refresh content="0;url=%s">'
            '<a href="%s">查看 %s 的诊断</a>'
            % (html_escape(s["name"]), dest, dest, html_escape(s["name"])), encoding="utf-8")
        print("  导出 %s %s（%d 张缩略图）" % (s["name"], s["started"], len(secs)), flush=True)
    return sessions, tree_raw, pend


def main():
    ap = argparse.ArgumentParser(description="导出静态站点")
    ap.add_argument("--out", default=str(ROOT / "out" / "site"))
    ap.add_argument("--max-thumbs", type=int, default=16, help="每场最多导几张缩略图（默认 16）")
    ap.add_argument("--days", type=int, default=30,
                    help="只导出最近 N 天的场次（默认 30；0 = 全部）。本地看板不受影响")
    a = ap.parse_args()
    if a.days > 0:
        cut = time.strftime("%Y%m%d", time.localtime(time.time() - a.days * 86400))
        scan_all = DASH.scan
        DASH.scan = lambda: [s for s in scan_all() if s["path"].rsplit("/", 1)[-1][:8] >= cut]
        print("只导出 %s 之后的场次（最近 %d 天）" % (cut, a.days), flush=True)

    final = pathlib.Path(a.out).resolve()
    out = final.with_name(final.name + ".building")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    print("导出场次 ...", flush=True)
    sessions, tree_raw, pend = export_sessions(out, a.max_thumbs)

    cats = sorted({s["category"] for s in DASH.scan()
                   if s["analyzed"] and s["group"] == "benchmark"})
    bench_sessions, benches = [], {}
    for c in cats:
        b = DASH.benchmark(c)
        benches[c] = b
        for x in b.get("sessions", []):
            pass
    for s in DASH.scan():
        if s["analyzed"] and s["group"] == "benchmark":
            bench_sessions.append({"path": s["path"], "name": s["name"],
                                   "category": s["category"], "started": s["started"],
                                   "elapsed": s["elapsed"], "chars": s["chars"]})
    bench_cats = []
    cnt = Counter(x["category"] for x in bench_sessions)
    for c, n in cnt.most_common():
        bench_cats.append({"category": c, "n": n})

    methods = {}
    mdir = ROOT / "data" / "method"
    if mdir.exists():
        for f in mdir.glob("*.json"):
            x = rj(f)
            if x:
                methods[x["category"]] = x

    session_list = [{"path": s["path"], "name": s["name"], "started": s["started"],
                     "group": s["group"], "analyzed": s["analyzed"]}
                    for s in DASH.scan()]

    visuals = {}
    for c in {s["category"] for s in DASH.scan() if DASH.rj(s["dir"] / "visual.json")}:
        visuals[c] = DASH.visual_profile(c)
    for v in visuals.values():
        for rel0, sec0 in collect_example_secs(
                {"e": v.get("examples"), "p": v.get("prop_examples")}):
            ensure_thumb(rel0, out, sec0)
    talks = {}
    for c in {s["category"] for s in DASH.scan() if DASH.rj(s["dir"] / "talk.json")}:
        talks[c] = DASH.talk_profile(c)
    talk_all = DASH.talk_profile()
    playbooks = {}
    for c in {s["category"] for s in DASH.scan()
              if s["group"] == "benchmark" and s["analyzed"]}:
        playbooks[c] = DASH.playbook(c)
    targets = {}
    for s0 in DASH.scan():
        if s0["group"] == "own" and s0["analyzed"]:
            tg = DASH.target_diagnosis(s0["path"])
            if tg and not tg.get("error"):
                targets[s0["path"]] = tg

    from datetime import datetime
    import time as _t
    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tree": tree_raw,
        "pending": pend,
        "sessions": sessions,
        "session_list": session_list,
        "bench_sessions": bench_sessions,
        "bench_cats": bench_cats,
        "benches": benches,
        "methods": methods,
        "method_cats": DASH.method_cats(),
        "tag_names": tag_names(),
        "dims_order": dims_order(),
        "core": [{"key": k, "name": n, "unit": u, "hi": h} for k, n, u, h in DASH.CORE],
        "visual_cats": DASH.visual_cats(),
        "visuals": visuals,
        "talk_cats": DASH.talk_cats(),
        "talks": talks,
        "talk_all_rows": talk_all.get("rows", []),
        "targets": targets,
        "anchor_list": DASH.anchor_view().get("anchors", []),
        "anchor_sel": {
            a["name"]: DASH.anchor_view(a["name"])["sel"]
            for a in DASH.anchor_view().get("anchors", [])
        },
        "playbooks": playbooks,
        "target_sessions": [{"path": s["path"], "name": s["name"],
                             "category": s["category"]}
                            for s in DASH.scan()
                            if s["group"] == "own" and s["analyzed"]],
        "total": {"sessions": len(session_list),
                  "analyzed": sum(1 for s in session_list if s["analyzed"]),
                  "danmu": sum(s["danmu"] for s in DASH.scan()),
                  "chars": sum(s["chars"] for s in DASH.scan())},
    }
    light = dict(data)
    light["sessions"] = {}
    for k, v in data["sessions"].items():
        light["sessions"][k] = {kk: v[kk] for kk in
                                ("path", "room", "name", "group", "category", "started",
                                 "elapsed", "danmu", "chars", "analyzed", "has_report",
                                 "frame_secs", "metrics", "tag_count", "evidence", "dims",
                                 "top", "risk", "transcript_chars", "rounds")}
    (out / "data.json").write_text(json.dumps(light, ensure_ascii=False),
                                   encoding="utf-8")
    det_dir = out / "sessiondata"
    det_dir.mkdir(exist_ok=True)
    for k, v in data["sessions"].items():
        (det_dir / (k.replace("/", "_") + ".json")).write_text(
            json.dumps(v, ensure_ascii=False), encoding="utf-8")

    html = (ROOT / "scripts" / "dashboard.html").read_text(encoding="utf-8")
    html = html.replace("<html ", "<html data-static=1 ", 1)
    page_html, page_js = DASH.split_page(html)
    (out / "index.html").write_text(page_html, encoding="utf-8")
    (out / "app.js").write_text(page_js, encoding="utf-8")

    shim = (
        '<!doctype html><meta charset=utf-8><title>跳转</title>'
        '<script>location.replace("../session.html" + location.search);</script>'
        '<a href="../session.html">回场次诊断</a>')
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "data" / "session.html").write_text(shim, encoding="utf-8")

    import subprocess
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make_session_page.py"), str(out)],
                   cwd=str(ROOT),
                     creationflags=CREATE_NO_WINDOW)

    export_docs(out)
    prev = final.with_name(final.name + ".prev")
    if prev.exists():
        shutil.rmtree(prev, ignore_errors=True)
    if final.exists():
        os.replace(final, prev)
    os.replace(out, final)
    shutil.rmtree(prev, ignore_errors=True)
    print("\n导出完成 -> %s" % final, flush=True)
    n_img = sum(1 for _ in out.rglob("*.jpg"))
    tot = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print("  %d 场 ・ %d 张缩略图 ・ 合计 %.1f MB" % (
        len(sessions), n_img, tot / 1048576), flush=True)


def export_docs(out):
    """把交付物导成静态文件。
    """
    import html as _h
    sys.path.insert(0, str(ROOT / "scripts"))
    from dashboard import md_to_html, doc_page, doc_manifest, DOC_ROOTS

    docs = out / "docs"
    raw = docs / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    back = "../index.html#/docs"
    groups, n = doc_manifest(), 0
    for g in groups:
        for it in g["items"]:
            if it["key"] == "health":
                txt = (ROOT / "logs" / "health_latest.txt").read_text(encoding="utf-8", errors="replace")
                (raw / "health.txt").write_text(txt, encoding="utf-8")
                (docs / "health.html").write_text(
                    doc_page("产出体检", "<h1>产出体检</h1><pre>%s</pre>" % _h.escape(txt), back),
                    encoding="utf-8")
            else:
                src = DOC_ROOTS[it["key"]][0] / it["file"]
                if it["kind"] == "md":
                    body = src.read_text(encoding="utf-8", errors="replace")
                    (raw / (it["key"] + "_" + it["file"])).write_text(body, encoding="utf-8")
                    (docs / (it["key"] + "_" + it["name"] + ".html")).write_text(
                        doc_page(it["name"], md_to_html(body), back), encoding="utf-8")
                else:
                    shutil.copy2(src, docs / (it["key"] + "_" + it["file"]))
            n += 1
    (docs / "index.json").write_text(json.dumps(groups, ensure_ascii=False), encoding="utf-8")
    (docs / "index.html").write_text(
        '<!doctype html><meta charset=utf-8><title>交付物</title>'
        '<meta http-equiv=refresh content="0;url=%s"><a href="%s">打开交付物</a>' % (back, back),
        encoding="utf-8")
    print("  交付物 %d 份 -> docs/" % n, flush=True)


if __name__ == "__main__":
    main()
