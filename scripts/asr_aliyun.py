#!/usr/bin/env python3
"""阿里云百炼 ASR 引擎（qwen-audio-3.0-asr-flash）
"""
import os, pathlib, subprocess, sys, tempfile, time
try:
    CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
except Exception:
    CREATE_NO_WINDOW = 0


ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / "config" / ".env.local"

CHUNK_SEC = 280
SENT_END = "。！？!?"
MAX_SENT_CHARS = 60


def load_env():
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import _config
    _config.load_env()


def audio_seconds(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
                     creationflags=CREATE_NO_WINDOW).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def split_wav(path, workdir, chunk=CHUNK_SEC):
    """切成不超过 chunk 秒的片段，返回 [(片段路径, 起始秒)]。"""
    total = audio_seconds(path)
    if total <= chunk:
        return [(path, 0.0)], total
    parts = []
    i = 0
    while i * chunk < total:
        off = i * chunk
        p = workdir / ("part%03d.wav" % i)
        subprocess.run(["ffmpeg", "-v", "error", "-ss", str(off), "-t", str(chunk),
                        "-i", str(path), "-ar", "16000", "-ac", "1", "-y", str(p)],
                       check=True,
                     creationflags=CREATE_NO_WINDOW)
        parts.append((p, float(off)))
        i += 1
    return parts, total


def call_one(wav, model, key, retries=3):
    from dashscope import MultiModalConversation
    uri = "file://" + str(pathlib.Path(wav).resolve()).replace("\\", "/")
    last = None
    for n in range(retries):
        try:
            r = MultiModalConversation.call(
                model=model, api_key=key,
                messages=[{"role": "user", "content": [{"audio": uri}]}],
                format="wav",
            )
            if r.status_code == 200:
                return r.output
            last = "%s %s" % (r.code, r.message)
        except Exception as e:
            last = str(e)
        time.sleep(1.5 * (n + 1))
    raise RuntimeError("ASR 调用失败：%s" % last)


def words_to_sentences(words, offset=0.0):
    """用字级时间戳 + 标点重建句子。服务端整段只给一个 sentence，
    直接用会得到横跨几分钟的巨型句子，后续分段和弹幕对齐都没法做。
    """
    out, buf = [], []
    for w in words:
        txt = (w.get("text") or "") + (w.get("punctuation") or "")
        if not txt:
            continue
        buf.append((txt, w.get("begin_time", 0), w.get("end_time", 0)))
        joined = "".join(x[0] for x in buf)
        if txt and txt[-1] in SENT_END or len(joined) >= MAX_SENT_CHARS:
            out.append({
                "start": round(buf[0][1] / 1000 + offset, 2),
                "end": round(buf[-1][2] / 1000 + offset, 2),
                "text": joined,
            })
            buf = []
    if buf:
        out.append({
            "start": round(buf[0][1] / 1000 + offset, 2),
            "end": round(buf[-1][2] / 1000 + offset, 2),
            "text": "".join(x[0] for x in buf),
        })
    return out


def extract(output, offset):
    """从响应里取 words；没有 words 时退回整段文本。"""
    inner = (output or {}).get("output") or {}
    sent = inner.get("sentence")
    if isinstance(sent, list):
        sent = sent[0] if sent else None
    words = (sent or {}).get("words") if sent else None
    if words:
        return words_to_sentences(words, offset)
    text = (output or {}).get("text") or ""
    return [{"start": offset, "end": offset, "text": text}] if text else []


def transcribe(wav, model=None, prompt="", verbose=True):
    load_env()
    key = os.environ.get("DASHSCOPE_API_KEY")
    model = model or os.environ.get("ASR_MODEL", "qwen-audio-3.0-asr-flash")
    if not key:
        raise SystemExit("缺少 DASHSCOPE_API_KEY，请写入 config/.env.local")

    with tempfile.TemporaryDirectory() as td:
        parts, total = split_wav(pathlib.Path(wav), pathlib.Path(td))
        if verbose:
            print("  引擎 %s  切片 %d 段（音频 %.0f 秒）" % (model, len(parts), total), flush=True)
        segs = []
        for i, (p, off) in enumerate(parts):
            out = call_one(p, model, key)
            got = extract(out, off)
            segs.extend(got)
            if verbose:
                print("    片段 %d/%d  偏移 %.0fs  %d 句" % (i + 1, len(parts), off, len(got)),
                      flush=True)
    segs.sort(key=lambda s: s["start"])
    return segs


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法: python scripts/asr_aliyun.py <wav文件>")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    t0 = time.time()
    r = transcribe(sys.argv[1])
    cost = time.time() - t0
    chars = sum(len(s["text"]) for s in r)
    print("\n%d 句 / %d 字，耗时 %.1f 秒" % (len(r), chars, cost))
    for s in r[:8]:
        print("  [%6.1f-%6.1f] %s" % (s["start"], s["end"], s["text"][:50]))
