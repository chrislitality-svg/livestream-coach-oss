#!/usr/bin/env python3
"""直播间采集：音频 + 弹幕 + 画面截图，带实时状态显示。

用法:
  python scripts/record.py douyin https://live.douyin.com/123456 --minutes 30
  python scripts/record.py douyin <url> --minutes 180 --shot-every 10
"""
import argparse, asyncio, base64, json, os, pathlib, shutil, subprocess, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PROFILE = ROOT / ".profiles"

SILENCE_RMS = 0.0005
SILENCE_STOP_MIN = 5
STARTUP_BUDGET_SEC = 180

CURRENT_OUTDIR = None

JS_START = r"""
async () => {
  const vs = [...document.querySelectorAll('video')];
  const ready = x => x.readyState >= 2 || x.videoWidth > 0;
  const v = vs.find(x => !isFinite(x.duration) && ready(x))
         || vs.find(ready) || vs.find(x => !isFinite(x.duration)) || vs[0];
  if (!v) return {ok:false, err:'页面上没有 video 元素'};
  if (!ready(v)) return {ok:false, err:'video 元素没有画面数据，多半没在推流'};
  v.muted = false; v.volume = 1;
  if (v.paused) { try { await v.play(); } catch(e) {} }

  const stream = v.captureStream ? v.captureStream() : null;
  if (!stream) return {ok:false, err:'captureStream 不可用'};
  const at = stream.getAudioTracks();
  if (!at.length) return {ok:false, err:'拿不到音轨，流可能被加密'};

  const mono = new MediaStream([at[0]]);
  const ctx = new AudioContext();
  const an = ctx.createAnalyser(); an.fftSize = 2048;
  ctx.createMediaStreamSource(mono).connect(an);
  const buf = new Float32Array(an.fftSize);

  const S = window.__rec = {
    rms: 0, peak: 0, bytes: 0, chunks: 0,
    danmu: [], danmuTotal: 0, lastDanmuAt: 0,
    startedAt: Date.now(), videoEl: v, err: '',
    ctx: ctx, stream: mono,
  };

  S.rmsTimer = setInterval(() => {
    an.getFloatTimeDomainData(buf);
    let s = 0;
    for (let i = 0; i < buf.length; i++) s += buf[i]*buf[i];
    S.rms = Math.sqrt(s / buf.length);
    if (S.rms > S.peak) S.peak = S.rms;
  }, 200);

  let mime = 'audio/webm;codecs=opus';
  if (!MediaRecorder.isTypeSupported(mime)) mime = 'audio/webm';
  const rec = new MediaRecorder(mono, {mimeType: mime, audioBitsPerSecond: 64000});
  rec.ondataavailable = async e => {
    if (!e.data || !e.data.size) return;
    S.bytes += e.data.size; S.chunks++;
    const b64 = await new Promise(res => {
      const fr = new FileReader();
      fr.onload = () => res(String(fr.result).split(',')[1]);
      fr.readAsDataURL(e.data);
    });
    try { await window.__onAudio(b64); } catch (err) { S.err = String(err); }
  };
  rec.start(1000);
  S.recorder = rec;

  {
    const seen = new Set();
    const push = el => {
      if (!el || el.nodeType !== 1) return;
      const cn = String(el.className || '');
      if (!cn.includes('item')) return;
      const txt = (el.innerText || '').replace(/\s+/g, ' ').trim();
      if (!txt || txt.length > 300) return;
      const body = el.querySelector('[class*="content-with-emoji-text"]');
      const content = body ? (body.innerText || '').replace(/\s+/g,' ').trim() : '';
      const nick = (content && txt.endsWith(content))
                 ? txt.slice(0, txt.length - content.length).replace(/[：:]\s*$/,'').trim()
                 : '';
      const key = nick + '|' + (content || txt);
      if (seen.has(key)) return;
      seen.add(key);
      if (seen.size > 5000) seen.clear();
      const srcs = [...el.querySelectorAll('img')].map(i => String(i.src || ''));
      const role = srcs.some(s => s.includes('anchor_badge')) ? 'anchor' : '';
      const lm = srcs.map(s => s.match(/grade_level_v\d+_(\d+)/)).find(Boolean);
      S.danmu.push({ts: Date.now(), nick: nick, text: content || txt, role: role,
                    lv: lm ? Number(lm[1]) : null});
      S.danmuTotal++; S.lastDanmuAt = Date.now();
    };
    const obs = new MutationObserver(ms => {
      for (const m of ms) {
        for (const n of m.addedNodes) {
          if (n.nodeType !== 1) continue;
          push(n);
          if (n.querySelectorAll) n.querySelectorAll('[class*="item"]').forEach(push);
        }
        if (m.type === 'characterData') {
          let e = m.target.parentElement, hop = 0;
          while (e && hop++ < 5) {
            if (String(e.className || '').includes('item')) { push(e); break; }
            e = e.parentElement;
          }
        }
      }
    });
    S.bindChat = () => {
      const best = document.querySelector('.webcast-chatroom');
      if (!best) {
        const tg = document.querySelector('[class*="chatroom_close"]');
        if (tg && Date.now() - (S.lastOpen || 0) > 10000) {
          tg.click(); S.lastOpen = Date.now(); S.opened = (S.opened || 0) + 1;
        }
      }
      if (S.chatRoot && S.chatRoot.isConnected && (!best || S.chatRoot === best)) return true;
      const l = document.querySelector('[class*="webcast-chatroom___list"]');
      const root = best || (l && l.parentElement);
      if (!root) return false;
      obs.disconnect();
      obs.observe(root, {childList:true, subtree:true, characterData:true});
      S.chatRoot = root; S.rebinds = (S.rebinds || 0) + 1;
      return true;
    };
    S.obs = obs;
    S.listFound = S.bindChat();
  }
  return {ok:true, mime:mime, w:v.videoWidth, h:v.videoHeight, listFound:S.listFound};
}
"""

JS_STATS = r"""
() => {
  const t = document.body.innerText || '';
  const num = m => m ? Math.round(parseFloat(m[1]) * (m[2] ? 10000 : 1)) : null;
  return {online: num(t.match(/在线观众\s*[·•・]?\s*([\d.]+)\s*(万)?/)),
          likes: num(t.match(/([\d.]+)\s*(万)?\s*本场点赞/))};
}
"""
STATS_EVERY = 30

JS_POLL = r"""
() => {
  const S = window.__rec;
  if (!S) return null;
  if (S.bindChat) S.listFound = S.bindChat();
  const d = S.danmu; S.danmu = [];
  const v = S.videoEl;
  return {rms:S.rms, peak:S.peak, bytes:S.bytes, chunks:S.chunks,
          danmuTotal:S.danmuTotal, lastDanmuAt:S.lastDanmuAt,
          newDanmu:d, listFound:S.listFound, err:S.err, chatOpened:S.opened||0,
          ctxState: S.ctx ? S.ctx.state : 'gone',
          videoAlive: !!(v && !v.ended && v.readyState >= 2)};
}
"""

JS_HEAL = r"""
async () => {
  const S = window.__rec;
  if (!S || !S.ctx) return {healed: false};
  try {
    if (S.ctx.state === 'suspended') await S.ctx.resume();
    if (S.rmsTimer) clearInterval(S.rmsTimer);
    const an = S.ctx.createAnalyser(); an.fftSize = 2048;
    S.ctx.createMediaStreamSource(S.stream).connect(an);
    const buf = new Float32Array(an.fftSize);
    S.rmsTimer = setInterval(() => {
      an.getFloatTimeDomainData(buf);
      let s = 0;
      for (let i = 0; i < buf.length; i++) s += buf[i]*buf[i];
      S.rms = Math.sqrt(s / buf.length);
      if (S.rms > S.peak) S.peak = S.rms;
    }, 200);
    return {healed: true, state: S.ctx.state};
  } catch (e) { return {healed: false, err: String(e)}; }
}
"""

JS_SHOT = r"""
() => {
  const S = window.__rec;
  const v = S && S.videoEl;
  if (!v || !v.videoWidth) return null;
  try {
    const c = document.createElement('canvas');
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext('2d').drawImage(v, 0, 0);
    return c.toDataURL('image/jpeg', 0.7).split(',')[1];
  } catch (e) { return 'ERR:' + e.name; }
}
"""

JS_STOP = r"""
() => {
  const S = window.__rec;
  if (!S) return;
  try { clearInterval(S.rmsTimer); } catch(e) {}
  try { S.recorder && S.recorder.stop(); } catch(e) {}
  try { S.obs && S.obs.disconnect(); } catch(e) {}
}
"""


def ensure_profile(platform, rid):
    """多路并发时每路必须有独立 profile —— Chromium 的 user-data-dir 是排他锁定的，
    共用会让第二个实例直接退出。
    """
    inst = PROFILE / ("%s_%s" % (platform, rid))
    inst.mkdir(parents=True, exist_ok=True)
    for rel in ("Default/Network/Cookies", "Default/Network/Cookies-journal", "Default/Cookies"):
        try:
            (inst / rel).unlink(missing_ok=True)
        except Exception:
            pass
    return inst


def other_recorder_alive(rid):
    """同一房间是否还有别的 record.py 在跑（不含自己）。
    """
    ps = ("Get-CimInstance Win32_Process "
          "-Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
          "Where-Object { $_.CommandLine -match 'record\\.py\\W+douyin' -and "
          "$_.CommandLine -match 'live\\.douyin\\.com/%s(\\D|$)' -and "
          "$_.ProcessId -ne %d } | Measure-Object | %% Count" % (rid, os.getpid()))
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=90)
        return int((out.stdout or "0").strip() or 0) > 0
    except Exception:
        return True


def reap_stale_profile(inst):
    """杀掉仍占着这份 profile 的遗留 chrome，返回清理掉的进程数。
    """
    esc = str(inst).replace("'", "''")
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
          "Where-Object { $_.CommandLine -like '*--user-data-dir=%s*' } | "
          "ForEach-Object { $_.ProcessId }" % esc)
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=90)
        pids = [ln.strip() for ln in (out.stdout or "").splitlines()
                if ln.strip().isdigit()]
        if not pids:
            return 0
        args = ["taskkill", "/F"]
        for pid in pids:
            args += ["/PID", pid]
        subprocess.run(args, capture_output=True, text=True, timeout=90)
        print("  清理遗留浏览器进程 %d 个（profile 被占锁）" % len(pids), flush=True)
        time.sleep(2)
        return len(pids)
    except Exception as e:
        print("  清理遗留进程失败: %s" % str(e)[:80], flush=True)
        return 0


def discard_if_empty(outdir):
    """一个字节音频都没落下的场次目录是纯垃圾，删掉。
    """
    if not outdir:
        return False
    outdir = pathlib.Path(outdir)
    if not outdir.exists():
        return False
    try:
        audio = outdir / "audio.webm"
        if audio.exists() and audio.stat().st_size > 0:
            return False
        if any((outdir / "frames").glob("*")):
            return False
        shutil.rmtree(outdir, ignore_errors=True)
        return not outdir.exists()
    except Exception:
        return False


def bar(rms, width=10):
    """音量条：0.15 以上算满格，人声一般在 0.02~0.15。"""
    n = min(width, int(rms / 0.15 * width + 0.5))
    return "#" * n + "." * (width - n)


def hms(sec):
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


async def run(platform, url, minutes, shot_every, headless, out=None):
    global CURRENT_OUTDIR
    rid = url.rstrip("/").split("/")[-1].split("?")[0]
    base = pathlib.Path(out).expanduser() if out else DATA
    outdir = base / platform / rid / time.strftime("%Y%m%d_%H%M%S")
    (outdir / "frames").mkdir(parents=True, exist_ok=True)
    CURRENT_OUTDIR = outdir
    audio_path = outdir / "audio.webm"
    danmu_path = outdir / "danmu.jsonl"

    audio_f = open(audio_path, "wb")
    danmu_f = open(danmu_path, "a", encoding="utf-8")
    tty = sys.stdout.isatty()
    state = {"written": 0}

    async def on_audio(_source, b64):
        try:
            audio_f.write(base64.b64decode(b64))
            audio_f.flush()
            state["written"] += 1
        except Exception as e:
            print("\n音频写入失败: %s" % e)

    async with async_playwright() as p:
        prof = ensure_profile(platform, rid)
        launch_kw = dict(
            user_data_dir=str(prof),
            headless=headless,
            args=["--autoplay-policy=no-user-gesture-required",
                  "--disable-blink-features=AutomationControlled",
                  "--disable-backgrounding-occluded-windows",
                  "--disable-renderer-backgrounding",
                  "--disable-background-timer-throttling",
                  "--disable-features=CalculateNativeWinOcclusion"],
            viewport={"width": 1280, "height": 820},
        )
        try:
            ctx = await p.chromium.launch_persistent_context(**launch_kw)
        except Exception as e:
            print("浏览器启动失败：%s" % str(e).splitlines()[0][:120], flush=True)
            if other_recorder_alive(rid):
                print("  同房间还有另一路 record.py 在跑，不清理 profile（避免误杀）",
                      flush=True)
                raise
            if not reap_stale_profile(prof):
                raise
            ctx = await p.chromium.launch_persistent_context(**launch_kw)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.set_default_timeout(60000)
        await page.expose_binding("__onAudio", on_audio)

        print("直播间 %s" % url, flush=True)
        print("输出   %s" % outdir, flush=True)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(12)

        try:
            r = await asyncio.wait_for(page.evaluate(JS_START),
                                       timeout=STARTUP_BUDGET_SEC)
        except asyncio.TimeoutError:
            print("启动超时：%d 秒内没拿到音轨（多半卡在 video.play()）"
                  % STARTUP_BUDGET_SEC, flush=True)
            await ctx.close(); audio_f.close(); danmu_f.close()
            return 1
        if not r.get("ok"):
            print("启动失败: %s" % r.get("err"), flush=True)
            await ctx.close(); audio_f.close(); danmu_f.close()
            return 1
        print("画面 %dx%d   弹幕容器 %s\n" % (
            r["w"], r["h"], "已找到" if r["listFound"] else "未找到 !!"), flush=True)
        try:
            title = (await page.title() or "").replace(" - 抖音直播", "").strip()
        except Exception:
            title = ""
        (outdir / "info.json").write_text(json.dumps(
            {"platform": platform, "url": url, "room_id": rid, "title": title,
             "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "width": r["w"], "height": r["h"]},
            ensure_ascii=False, indent=1), encoding="utf-8")

        t0 = time.time()
        deadline = t0 + minutes * 60
        last_shot = 0.0
        last_stat = 0.0
        chat_opened = 0
        shots = 0
        silent_since = None
        last_heal = 0.0
        stop_reason = "到达设定时长"

        while time.time() < deadline:
            await asyncio.sleep(1)
            s = await page.evaluate(JS_POLL)
            if not s:
                stop_reason = "页面状态丢失（可能被刷新）"; break

            chat_opened = s.get("chatOpened", 0)
            for d in s["newDanmu"]:
                danmu_f.write(json.dumps(d, ensure_ascii=False) + "\n")
            if s["newDanmu"]:
                danmu_f.flush()

            now = time.time()
            if now - last_shot >= shot_every:
                b64 = await page.evaluate(JS_SHOT)
                if b64 and not b64.startswith("ERR:"):
                    (outdir / "frames" / ("%06d.jpg" % int(now - t0))).write_bytes(
                        base64.b64decode(b64))
                    shots += 1
                elif b64:
                    print("\n截图失败(%s)，后续改用页面截图" % b64)
                last_shot = now

            if now - last_stat >= STATS_EVERY:
                last_stat = now
                try:
                    st = await page.evaluate(JS_STATS)
                    if st and (st["online"] is not None or st["likes"] is not None):
                        with open(outdir / "stats.jsonl", "a", encoding="utf-8") as f:
                            f.write(json.dumps({"t": round(now - t0, 1), **st}) + "\n")
                except Exception:
                    pass

            if not s["videoAlive"]:
                stop_reason = "视频流结束（主播下播）"; break
            if s["rms"] < SILENCE_RMS:
                silent_since = silent_since or now
                if now - silent_since > 90 and now - last_heal > 60:
                    h = await page.evaluate(JS_HEAL)
                    last_heal = now
                    print("\n[自愈] %s" % json.dumps(h, ensure_ascii=False), flush=True)
                if now - silent_since > SILENCE_STOP_MIN * 60:
                    stop_reason = "连续静音 %d 分钟" % SILENCE_STOP_MIN; break
            else:
                silent_since = None

            el = int(now - t0)
            ago = int((time.time()*1000 - s["lastDanmuAt"]) / 1000) if s["lastDanmuAt"] else -1
            line = ("[%s] 音量 |%s| %.3f   音频 %.1fMB   弹幕 %d条 %s   截图 %d"
                    % (hms(el), bar(s["rms"]), s["rms"], s["bytes"] / 1048576,
                       s["danmuTotal"],
                       ("%d/分 %ds前" % (s["danmuTotal"] * 60 // max(el, 1), ago))
                       if ago >= 0 else "(无)",
                       shots))
            if tty:
                sys.stdout.write("\r" + line + "   ")
                sys.stdout.flush()
            elif el % 30 == 0:
                print(line, flush=True)

        await page.evaluate(JS_STOP)
        await asyncio.sleep(1.5)
        final = await page.evaluate(JS_POLL) or {}
        for d in final.get("newDanmu", []):
            danmu_f.write(json.dumps(d, ensure_ascii=False) + "\n")
        await ctx.close()

    audio_f.close(); danmu_f.close()
    dur = int(time.time() - t0)
    (outdir / "meta.json").write_text(json.dumps({
        "platform": platform, "url": url, "room_id": rid,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
        "duration_sec": dur, "stop_reason": stop_reason,
        "audio_bytes": audio_path.stat().st_size,
        "danmu_count": sum(1 for _ in open(danmu_path, encoding="utf-8")),
        "danmu_capture": 2,
        "chat_opened": chat_opened,
        "frames": shots,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n\n结束：%s" % stop_reason)
    print("  时长 %s   音频 %.1fMB   弹幕 %d 条   截图 %d 张" % (
        hms(dur), audio_path.stat().st_size / 1048576,
        sum(1 for _ in open(danmu_path, encoding="utf-8")), shots))
    print("  %s" % outdir)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="直播间采集（音频+弹幕+截图）")
    ap.add_argument("platform")
    ap.add_argument("url")
    ap.add_argument("--minutes", type=float, default=30, help="录制时长，默认 30 分钟")
    ap.add_argument("--shot-every", type=int, default=10, help="截图间隔秒，默认 10")
    ap.add_argument("--headless", action="store_true",
                    help="无头运行。实测平台在无头下不推送弹幕，仅在只要音频时用")
    ap.add_argument("--out", help="采集数据根目录，默认 <项目>/data")
    a = ap.parse_args()

    async def guarded():
        return await asyncio.wait_for(
            run(a.platform, a.url, a.minutes, a.shot_every, a.headless, a.out),
            timeout=(a.minutes + 10) * 60)

    try:
        rc = asyncio.run(guarded())
    except asyncio.TimeoutError:
        print("\n录制超时（超过设定时长 10 分钟仍未结束），强制终止", flush=True)
        rc = 1
    except Exception:
        import traceback
        traceback.print_exc()
        rc = 1
    if rc != 0 and discard_if_empty(CURRENT_OUTDIR):
        print("本场没产出任何数据，已清理空目录", flush=True)
    sys.exit(rc)
