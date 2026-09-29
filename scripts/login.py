#!/usr/bin/env python3
"""人工登录一次，登录态存进 profile，供后续无人值守采集复用。

用法:
  python scripts/login.py douyin
  python scripts/login.py kuaishou
"""
import argparse, asyncio, pathlib, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".profiles"

HOME = {
    "douyin": "https://live.douyin.com/",
    "kuaishou": "https://live.kuaishou.com/",
}

TOKENS = ("sessionid", "web_st", "passtoken", "pass_token")


async def wait_login(ctx, timeout_s):
    """轮询 cookie，出现登录标识即返回。"""
    for _ in range(timeout_s // 3):
        names = {c["name"].lower() for c in await ctx.cookies()}
        hit = [n for n in names if any(t in n for t in TOKENS)]
        if hit:
            return hit
        await asyncio.sleep(3)
    return []


async def main(platform, timeout_s):
    PROFILE.mkdir(exist_ok=True)
    url = HOME.get(platform, HOME["douyin"])
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE / platform),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 860},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        names = {c["name"].lower() for c in await ctx.cookies()}
        if any(any(t in n for t in TOKENS) for n in names):
            print("该 profile 已是登录状态，无需重复登录。")
            print("（想换账号就删掉 %s 再跑一次）" % (PROFILE / platform))
            await ctx.close()
            return 0

        print("浏览器已打开，请在窗口里完成登录（扫码即可）。")
        print("登录成功后本脚本会自动检测并退出，最多等 %d 秒。" % timeout_s)
        hit = await wait_login(ctx, timeout_s)
        await ctx.close()

        if hit:
            print("\n登录成功，凭据已保存到 %s" % (PROFILE / platform))
            print("检测到的登录标识: %s" % ", ".join(sorted(hit)))
            print("下一步可跑 check_capture.py 确认弹幕是否开始推送。")
            return 0
        print("\n超时未检测到登录状态。请重跑，或确认扫码是否完成。")
        return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="登录并保存采集用的浏览器凭据")
    ap.add_argument("platform", choices=sorted(HOME), help="平台")
    ap.add_argument("--timeout", type=int, default=300, help="等待登录的秒数，默认 300")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.platform, a.timeout)))
