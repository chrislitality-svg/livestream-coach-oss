#!/usr/bin/env python3
"""P0 架构验证：能否直接从直播间页面内捕获主播音频。

用法:
  python scripts/check_capture.py douyin https://live.douyin.com/123456
  python scripts/check_capture.py kuaishou https://live.kuaishou.com/u/xxx --seconds 20
"""
import argparse, asyncio, base64, pathlib, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
PROFILE = ROOT / ".profiles"

RMS_ALIVE = 0.002
RMS_CLEAR = 0.01

JS_PROBE = r"""
async ([seconds]) => {
  const log = [];
  const vs = [...document.querySelectorAll('video')];
  if (!vs.length) return {ok:false, stage:'video', err:'页面上找不到 video 元素'};
  const v = vs.find(x => !isFinite(x.duration)) || vs[0];
  log.push('页面共 ' + vs.length + ' 个 video，选中 duration=' + v.duration);
  log.push('初始状态 readyState=' + v.readyState + ' paused=' + v.paused +
           ' muted=' + v.muted + ' volume=' + v.volume);

  v.muted = false;
  v.volume = 1;
  if (v.paused) {
    try { await v.play(); log.push('已调用 play()'); }
    catch (e) { log.push('play() 被拒绝: ' + e.message); }
  }

  const t1 = performance.now();
  while (v.readyState < 2 && performance.now() - t1 < 10000) {
    await new Promise(r => setTimeout(r, 200));
  }
  if (v.readyState < 2) {
    return {ok:false, stage:'video', err:'视频流始终未就绪 readyState=' + v.readyState, log};
  }

  let stream;
  try {
    stream = v.captureStream ? v.captureStream()
           : (v.mozCaptureStream ? v.mozCaptureStream() : null);
  } catch (e) {
    return {ok:false, stage:'captureStream', err:'captureStream 抛异常: ' + e.message, log};
  }
  if (!stream) return {ok:false, stage:'captureStream', err:'浏览器不支持 captureStream', log};

  const tracks = stream.getAudioTracks();
  if (!tracks.length) {
    return {ok:false, stage:'audioTrack',
            err:'音轨为空 —— 流可能被加密(DRM)或源本身无声', log};
  }
  const track = tracks[0];
  log.push('音轨 label="' + track.label + '" state=' + track.readyState + ' muted=' + track.muted);
  const mono = new MediaStream([track]);

  const ctx = new AudioContext();
  const an = ctx.createAnalyser();
  an.fftSize = 2048;
  ctx.createMediaStreamSource(mono).connect(an);
  const buf = new Float32Array(an.fftSize);

  let mime = 'audio/webm;codecs=opus';
  if (!MediaRecorder.isTypeSupported(mime)) mime = 'audio/webm';
  if (!MediaRecorder.isTypeSupported(mime)) {
    ctx.close();
    return {ok:false, stage:'recorder', err:'MediaRecorder 不支持 webm', log};
  }

  const chunks = [];
  let rec;
  try { rec = new MediaRecorder(mono, {mimeType: mime, audioBitsPerSecond: 64000}); }
  catch (e) {
    ctx.close();
    return {ok:false, stage:'recorder', err:'MediaRecorder 创建失败: ' + e.message, log};
  }
  rec.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
  rec.start(1000);

  let peak = 0, sum = 0, n = 0, silent = 0;
  const t0 = performance.now();
  while (performance.now() - t0 < seconds * 1000) {
    an.getFloatTimeDomainData(buf);
    let s = 0;
    for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
    const rms = Math.sqrt(s / buf.length);
    if (rms < 0.0005) silent++;
    if (rms > peak) peak = rms;
    sum += rms; n++;
    await new Promise(r => setTimeout(r, 100));
  }

  await new Promise(res => { rec.onstop = res; try { rec.stop(); } catch (e) { res(); } });
  ctx.close();

  const blob = new Blob(chunks, {type: mime});
  const b64 = blob.size ? await new Promise(res => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result).split(',')[1]);
    fr.readAsDataURL(blob);
  }) : '';

  return {ok:true, mime:mime, bytes:blob.size, chunks:chunks.length,
          peakRms:peak, avgRms:(n ? sum / n : 0), silentRatio:(n ? silent / n : 1),
          samples:n, b64:b64, log:log};
}
"""


JS_DANMU = r"""
async ([seconds]) => {
  const sels = ['[class*="webcast-chatroom___list"]', '[class*="chat-list"]',
                '[class*="barrage"]', '[class*="comment-list"]', '[class*="chatroom"]'];
  let list = null, usedSel = '';
  for (const s of sels) {
    const e = document.querySelector(s);
    if (e && (e.innerText || '').trim()) { list = e; usedSel = s; break; }
  }
  if (!list) return {found:false, err:'未找到弹幕容器'};

  const seen = new Set();
  let added = 0;
  const obs = new MutationObserver(ms => {
    for (const m of ms) for (const n of m.addedNodes) {
      if (n.nodeType !== 1) continue;
      const t = (n.innerText || '').replace(/\s+/g, ' ').trim();
      if (t && t.length < 200 && !seen.has(t)) { seen.add(t); added++; }
    }
  });
  const lenStart = list.innerText.length;
  obs.observe(list, {childList:true, subtree:true});
  await new Promise(r => setTimeout(r, seconds * 1000));
  obs.disconnect();

  return {found:true, selector:usedSel, newMessages:added,
          perMinute: Math.round(added / seconds * 60),
          lenStart:lenStart, lenEnd:list.innerText.length,
          samples:[...seen].slice(0, 5)};
}
"""


async def probe(platform, url, seconds, wait, headless):
    OUT.mkdir(exist_ok=True)
    PROFILE.mkdir(exist_ok=True)
    async with async_playwright() as p:
        from record import ensure_profile
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(ensure_profile(platform, "check")),
            headless=headless,
            args=[
                "--autoplay-policy=no-user-gesture-required",
                "--disable-blink-features=AutomationControlled",
            ],
            viewport={"width": 1280, "height": 820},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.set_default_timeout(60000)

        print("[1/5] 打开 " + url)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print("  页面加载异常（继续尝试）: %s" % e)

        print("[2/5] 等待 %ds 让直播流起来（有登录/下载弹窗可手动关掉）" % wait)
        await asyncio.sleep(wait)

        print("[3/5] 查找 video 元素")
        try:
            await page.wait_for_selector("video", timeout=30000)
        except Exception:
            print("  !! 没找到 video 元素，可能未进入直播间或主播已下播")
            await ctx.close()
            return None

        print("[4/5] 捕获并录制 %ds ..." % seconds)
        budget = seconds + 60
        try:
            r = await asyncio.wait_for(page.evaluate(JS_PROBE, [seconds]),
                                       timeout=budget)
        except asyncio.TimeoutError:
            print("  !! 探测超时：%d 秒内没拿到结果，多半卡在 video.play()" % budget)
            await ctx.close()
            return None

        print("[5/5] 监听弹幕 %ds ..." % seconds)
        try:
            r["danmu"] = await page.evaluate(JS_DANMU, [seconds])
        except Exception as e:
            r["danmu"] = {"found": False, "err": str(e)}

        if r.get("ok") and r.get("b64"):
            path = OUT / ("check_%s_%s.webm" % (platform, time.strftime("%Y%m%d_%H%M%S")))
            path.write_bytes(base64.b64decode(r["b64"]))
            r["path"] = str(path.relative_to(ROOT))
        r.pop("b64", None)
        await ctx.close()
        return r


def report(platform, r, seconds):
    print("\n" + "=" * 56)
    if r is None:
        print("[%s] 验证失败：页面上没有 video 元素" % platform)
        print("=" * 56)
        return False

    for line in r.get("log", []):
        print("  · " + line)

    d = r.get("danmu") or {}
    print("\n  -- 弹幕 --")
    if not d.get("found"):
        print("  未找到弹幕容器：%s" % d.get("err", ""))
    else:
        print("  容器 %s" % d["selector"])
        print("  %ds 内新增 %d 条（约 %d 条/分钟）" % (seconds, d["newMessages"], d["perMinute"]))
        print("  容器文本长度 %d -> %d" % (d["lenStart"], d["lenEnd"]))
        for s in d.get("samples", []):
            print("    > " + s[:50])
        if d["newMessages"] == 0:
            if d["lenEnd"] != d["lenStart"]:
                print("  !! 文本在变但没记到新增 —— 平台用节点复用更新弹幕，")
                print("     需要改监听 characterData 而非只监听 childList。")
            else:
                print("  !! 容器完全没动。要么这个直播间当前无人发言（换带货间复测），")
                print("     要么登录态失效导致平台不推送弹幕。")

    if not r.get("ok"):
        print("\n[%s] 验证不通过 —— 卡在 %s 环节" % (platform, r.get("stage")))
        print("  原因: %s" % r.get("err"))
        if r.get("stage") == "audioTrack":
            print("\n  这是最坏情况：音频流受保护，页面内拿不到。")
            print("  回落方案：虚拟声卡分流 + 系统回环录制（一机一路，吞吐下降）")
        print("=" * 56)
        return False

    print("\n  录制格式   %s" % r["mime"])
    print("  数据量     %.1f KB / %ds（%d 个分块）" % (r["bytes"] / 1024, seconds, r["chunks"]))
    print("  平均音量   %.4f" % r["avgRms"])
    print("  峰值音量   %.4f" % r["peakRms"])
    print("  静音占比   %.1f%%" % (r["silentRatio"] * 100))
    if r.get("path"):
        print("  已存盘     %s  <- 可直接播放确认是主播的声音" % r["path"])

    ok = r["bytes"] > 1024 and r["avgRms"] > RMS_ALIVE
    print()
    if not ok and r["bytes"] <= 1024:
        print("[%s] 验证不通过：几乎没有录到数据，MediaRecorder 未正常工作" % platform)
    elif not ok:
        print("[%s] 验证不通过：拿到了音轨但全程接近静音" % platform)
        print("  可能原因：页面静音未解除、主播当前无声、或音频未走 video 元素")
    elif r["peakRms"] < RMS_CLEAR:
        print("[%s] 基本通过，但音量偏低 —— 建议换一个正在讲话的直播间复测" % platform)
    else:
        print("[%s] 验证通过：页面内录音方案成立，可支持多路并发采集" % platform)
    print("=" * 56)
    return ok


def main():
    ap = argparse.ArgumentParser(description="P0 架构验证：页面内音频捕获")
    ap.add_argument("platform", help="平台标识，如 douyin / kuaishou")
    ap.add_argument("url", help="直播间地址")
    ap.add_argument("--seconds", type=int, default=15, help="录制时长，默认 15 秒")
    ap.add_argument("--wait", type=int, default=10, help="页面加载等待，默认 10 秒")
    ap.add_argument("--headless", action="store_true", help="无头模式（首次建议有头，便于关弹窗）")
    a = ap.parse_args()

    r = asyncio.run(probe(a.platform, a.url, a.seconds, a.wait, a.headless))
    sys.exit(0 if report(a.platform, r, a.seconds) else 1)


if __name__ == "__main__":
    main()
