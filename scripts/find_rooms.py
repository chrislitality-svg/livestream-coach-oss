#!/usr/bin/env python3
"""列出当前在播的直播间，标出哪些是带货间（挂了小黄车）。

用法:
  python scripts/find_rooms.py                # 只列首页推荐
  python scripts/find_rooms.py --check 6      # 再逐个进去确认是否带货
"""
import argparse, asyncio, pathlib, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".profiles"

JS_LIST = r"""
() => {
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const h = a.getAttribute('href') || '';
    const m = h.match(/^\/(\d{6,})/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    out.push({rid: m[1], text: (a.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 50)});
  }
  if (!out.length) {
    const html = document.documentElement.innerHTML;
    for (const m of html.matchAll(/live\.douyin\.com\\?\/(\d{6,})/g)) {
      if (!seen.has(m[1])) { seen.add(m[1]); out.push({rid: m[1], text: ''}); }
    }
  }
  return out;
}
"""

JS_ECOM = r"""
() => {
  const vs = [...document.querySelectorAll('video')];
  const live = vs.find(v => !isFinite(v.duration));
  const sels = ['[class*="ecom"]', '[class*="goods"]', '[class*="shop"]',
                '[class*="promotion"]', '[class*="cart"]'];
  let hit = 0;
  for (const s of sels) hit += document.querySelectorAll(s).length;
  const txt = document.body.innerText || '';
  const kw = ['购物车', '小黄车', '去购买', '立即购买', '已售'].filter(k => txt.includes(k));
  return {
    living: !!live,
    title: document.title.replace(' - 抖音直播', ''),
    ecomNodes: hit,
    keywords: kw,
    isEcom: hit > 0 || kw.length > 0
  };
}
"""


STRONG = {
    "手机数码": ["手机", "平板", "耳机", "手表", "充电宝", "笔记本", "电脑", "数码",
             "iphone", "pad", "watch"],
    "厨房小家电": ["电饭煲", "炊具", "破壁机", "空气炸", "电压力", "厨房", "炒菜",
              "料理机", "电磁炉", "烤箱", "微波炉", "炖锅", "煮锅"],
    "大家电": ["电视", "彩电", "冰箱", "洗衣机", "空调", "洗碗机", "热水器",
            "油烟机", "烟机", "燃气灶", "灶具", "集成灶", "大家电", "冰柜"],
    "清洁电器": ["扫地机", "吸尘器", "洗地机", "拖把"],
    "个护电器": ["剃须刀", "吹风机", "牙刷", "美容仪", "卷发"],
    "智能家居": ["门锁", "摄像头", "智能家居"],
}
WEAK = {}
EXCLUDE = ["cs2", "csgo", "cf", "三角洲", "和平精英", "王者", "游戏", "陪玩",
           "户外", "唱歌", "舞蹈", "超市", "大米", "母婴", "鼠标"]


def classify(title):
    t = (title or "").lower()
    if any(x in t for x in EXCLUDE):
        return None
    for cat, kws in STRONG.items():
        if any(k in t for k in kws):
            return cat
    for cat, kws in WEAK.items():
        if any(k in t for k in kws):
            return cat
    return None


JS_SEARCH = r"""
() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href*="live.douyin.com/"]')) {
    const m = (a.href || '').match(/live\.douyin\.com\/(\d{6,})/);
    if (m && !seen.has(m[1])) {
      seen.add(m[1]);
      out.push({rid: m[1], text: (a.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40)});
    }
  }
  return out;
}
"""


async def search_rooms(page, kw):
    """首页推荐的品类分布随机，搜索才能定向找到某个品类的直播间。"""
    import urllib.parse
    url = "https://www.douyin.com/search/" + urllib.parse.quote(kw) + "?type=live"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(7)
        for _ in range(2):
            await page.mouse.wheel(0, 2400)
            await asyncio.sleep(2)
        return await page.evaluate(JS_SEARCH)
    except Exception as e:
        print("  搜索 %s 失败: %s" % (kw, str(e)[:50]), flush=True)
        return []


async def main(limit, check, want_cat=None, rounds=1, kws=None):
    async with async_playwright() as p:
        from record import ensure_profile
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(ensure_profile("douyin", "findrooms")),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 820},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.set_default_timeout(45000)
        rooms, seen = [], set()
        if kws:
            for kw in kws:
                got = await search_rooms(page, kw)
                fresh = [x for x in got if x["rid"] not in seen]
                for x in fresh:
                    seen.add(x["rid"]); rooms.append(x)
                print("搜索「%s」新增 %d 个（累计 %d）" % (kw, len(fresh), len(rooms)), flush=True)
        for i in range(rounds if not kws else 0):
            await page.goto("https://live.douyin.com/?r=%d" % i, wait_until="domcontentloaded")
            await asyncio.sleep(6)
            for r in await page.evaluate(JS_LIST):
                if r["rid"] not in seen:
                    seen.add(r["rid"]); rooms.append(r)
            print("第 %d 轮：累计 %d 个直播间" % (i + 1, len(rooms)), flush=True)
        rooms = rooms[:limit]

        if not check:
            for r in rooms:
                print("  %s  %s" % (r["rid"], r["text"]))
            await ctx.close()
            return

        print("逐个检查前 %d 个是否带货 ...\n" % min(check, len(rooms)))
        found = []
        for r in rooms[:check]:
            url = "https://live.douyin.com/" + r["rid"]
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(5)
                info = await page.evaluate(JS_ECOM)
            except Exception as e:
                print("  %s  打开失败 %s" % (r["rid"], str(e)[:40]))
                continue
            cat = classify(info["title"])
            live = "在播" if info["living"] else "已下播"
            ok = info["isEcom"] and info["living"] and cat and (not want_cat or cat == want_cat)
            print("  %s  [%s/%s]  %s" % (r["rid"], live, cat or "不符", info["title"][:26]),
                  flush=True)
            if ok:
                found.append((r["rid"], info["title"], cat))

        await ctx.close()
        print()
        if found:
            print("可用于测试的带货直播间：")
            for rid, t, c in found:
                print("  %-14s %-8s %s" % (rid, c, t[:28]))
            import json as _j
            pathlib.Path("out_rooms.json").write_text(_j.dumps(
                [{"room_id": r, "name": t.replace("的抖音直播间", ""), "category": c}
                 for r, t, c in found], ensure_ascii=False, indent=1), encoding="utf-8")
            print("")
            print("已写入 out_rooms.json")
        else:
            print("没找到在播的带货间，稍后重试或换时段。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="列出在播直播间并标出带货间")
    ap.add_argument("--limit", type=int, default=20, help="最多列出多少个")
    ap.add_argument("--check", type=int, default=0, help="逐个进去检查前 N 个是否带货")
    ap.add_argument("--category", help="只要这个品类，如 手机数码")
    ap.add_argument("--rounds", type=int, default=1, help="刷新首页几轮以收集更多直播间")
    ap.add_argument("--search", help="搜索词，逗号分隔，如 投影仪,电饭煲")
    a = ap.parse_args()
    asyncio.run(main(a.limit, a.check, a.category, a.rounds,
                     [x.strip() for x in a.search.split(',')] if a.search else None))
