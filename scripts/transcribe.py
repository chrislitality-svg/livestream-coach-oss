#!/usr/bin/env python3
"""语音转写：录制目录 -> transcript.json

用法:
  python scripts/transcribe.py <录制目录>
  python scripts/transcribe.py <录制目录> --model large-v3 --prompt "某品牌电饭煲"
"""
import argparse, json, pathlib, re, subprocess, sys, time
try:
    CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
except Exception:
    CREATE_NO_WINDOW = 0


try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _enable_cuda_dlls():
    """pip 装的 NVIDIA 运行库不在 Windows 的 DLL 搜索路径里，不手动挂上
    ctranslate2 就找不到 cublas/cudnn，GPU 会静默不可用。必须在导入前执行。
    """
    try:
        import os, nvidia
        base = pathlib.Path(list(nvidia.__path__)[0])
        for sub in ("cublas/bin", "cudnn/bin", "cuda_nvrtc/bin"):
            d = base / sub
            if d.is_dir():
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


_enable_cuda_dlls()


def to_wav(webm, wav):
    """webm/opus -> 16k 单声道 wav，各家 ASR 都吃这个格式。"""
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(webm),
                    "-ar", "16000", "-ac", "1", "-y", str(wav)], check=True,
                     creationflags=CREATE_NO_WINDOW)
    return wav


def _has_cuda():
    """真检测：CUDA 运行库常缺失(cublas/cudnn)，光看设备存在不算数。"""
    try:
        import ctranslate2
        return bool(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        return False


def _run(wav, model_size, prompt, dev, ct):
    from faster_whisper import WhisperModel
    m = WhisperModel(model_size, device=dev, compute_type=ct)
    segs, _ = m.transcribe(
        str(wav), language="zh", beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        initial_prompt=prompt or None,
        condition_on_previous_text=False,
    )
    out = []
    for s in segs:
        t = (s.text or "").strip()
        if not t:
            continue
        out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": t})
        if len(out) % 20 == 0:
            print("    已转写 %d 句 (%.0fs)" % (len(out), s.end), flush=True)
    return out


LOCK = pathlib.Path(__file__).resolve().parent.parent / "logs" / ".asr.lock"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _config
NANO = _config.model_dir("nano")


def transcribe_nano(wav, prompt=""):
    """FunASR-Nano（阿里开源，SenseVoice 格式导出，sherpa-onnx 在 CPU 上跑）。
    """
    import wave, numpy as np, sherpa_onnx
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    if not (NANO / "model.int8.onnx").exists():
        raise RuntimeError("找不到 FunASR-Nano 模型：%s" % NANO)
    rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(NANO / "model.int8.onnx"), tokens=str(NANO / "tokens.txt"),
        num_threads=max(1, (__import__("os").cpu_count() or 8) // 2), language="zh", use_itn=True)
    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        nf = max(wav.stat().st_size - 44, 0) // (w.getsampwidth() * w.getnchannels())
        audio = np.frombuffer(w.readframes(nf), dtype=np.int16).astype(np.float32) / 32768.0
    segs = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=500,
                                                   max_speech_duration_s=30))
    print("  引擎 FunASR-Nano/CPU  VAD 切出 %d 段" % len(segs), flush=True)
    out = []
    for i, s in enumerate(segs, 1):
        st = rec.create_stream()
        st.accept_waveform(sr, audio[s["start"]:s["end"]])
        rec.decode_stream(st)
        t = st.result.text.strip()
        if t:
            out.append({"start": round(s["start"] / sr, 2), "end": round(s["end"] / sr, 2), "text": t})
        if i % 100 == 0:
            print("    已转写 %d/%d 段 (%.0fs)" % (i, len(segs), s["end"] / sr), flush=True)
    return out


class _AsrLock:
    """转写的跨进程锁：全系统同一时刻只跑一路转写，不管用的是显卡还是 CPU。
    """
    waited = 0

    def __enter__(self):
        import msvcrt
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(LOCK, "a+")
        waited = 0
        while True:
            try:
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                if waited:
                    print("  排队 %d 秒后开始转写" % waited, flush=True)
                _AsrLock.waited = waited
                return self

            except OSError:
                if not waited:
                    print("  另一场转写正在进行，排队等待 ...", flush=True)
                time.sleep(5)
                waited += 5

    def __exit__(self, *exc):
        import msvcrt
        try:
            self.f.seek(0)
            msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self.f.close()


def transcribe_local(wav, model_size, prompt):
    plans = ([("cuda", "int8_float16")] if _has_cuda() else []) + [("cpu", "int8")]
    last = None
    for dev, ct in plans:
        print("  引擎 faster-whisper/%s  设备 %s/%s" % (model_size, dev, ct), flush=True)
        try:
            if dev == "cuda":
                with _AsrLock():
                    return _run(wav, model_size, prompt, dev, ct)
            return _run(wav, model_size, prompt, dev, ct)
        except Exception as e:
            last = e
            print("  %s 失败: %s" % (dev, str(e)[:70]), flush=True)
            if dev == "cpu":
                raise
    raise last


def transcribe_aliyun(wav, prompt):
    """阿里云百炼。密钥读 config/.env.local，长音频自动切片并拼接时间轴。"""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import asr_aliyun
    return asr_aliyun.transcribe(wav, prompt=prompt)


def main():
    ap = argparse.ArgumentParser(description="直播录音转写")
    ap.add_argument("recdir", help="record.py 的输出目录")
    ap.add_argument("--engine", default="local", choices=["local", "nano", "aliyun"])
    ap.add_argument("--model", default="small", help="本地模型：small/medium/large-v3")
    ap.add_argument("--prompt", default="", help="场景提示词，写上品牌名和品类可提升专有名词准确率")
    ap.add_argument("--post-only", action="store_true",
                    help="不转写，只对已有 transcript.json 重做后处理：同音纠错 + 标点")
    ap.add_argument("--keep-wav", action="store_true", help="转写完不删 audio.wav（默认删，能从 webm 秒级重建）")
    a = ap.parse_args()

    d = pathlib.Path(a.recdir).expanduser()
    if a.post_only:
        tp = d / "transcript.json"
        tr = json.loads(tp.read_text(encoding="utf-8"))
        tr["term_fixes"] = fix_terms(tr.get("items") or [])
        tr["punct_model"] = punctuate(tr.get("items") or [])
        tmp = tp.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(tr, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(tp)
        print("后处理完成：纠错 %d 句，标点 %d 句 -> %s"
              % (tr["term_fixes"], len(tr.get("items") or []), tp))
        return
    webm = d / "audio.webm"
    if not webm.exists():
        raise SystemExit("找不到 %s" % webm)

    wav = d / "audio.wav"
    if not wav.exists():
        print("[1/2] 转码 wav ...", flush=True)
        to_wav(webm, wav)
    else:
        print("[1/2] 复用已有 wav", flush=True)

    import wave
    with wave.open(str(wav)) as w:
        rate, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
    dur = max(wav.stat().st_size - 44, 0) / (rate * ch * sw)
    print("  音频时长 %.1f 秒" % dur, flush=True)

    live = d / "live.jsonl"
    if live.exists() and a.engine == "aliyun":
        rows = []
        for ln in live.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
        cov = max((r["end"] for r in rows), default=0)
        if rows and cov >= dur * 0.9:
            segs = [{"start": r["start"], "end": r["end"], "text": r["text"]} for r in rows]
            segs.sort(key=lambda x: x["start"])
            print("  复用边录边转结果：%d 句，覆盖 %.0f/%.0f 秒" % (len(segs), cov, dur), flush=True)
            _write(d, a, segs, dur, 0.0)
            return

    print("[2/2] 转写中 ...", flush=True)
    _AsrLock.waited = 0
    t0 = time.time()
    if a.engine == "local":
        segs = transcribe_local(wav, a.model, a.prompt)
    elif a.engine == "nano":
        with _AsrLock():
            segs = transcribe_nano(wav, a.prompt)
    else:
        segs = transcribe_aliyun(wav, a.prompt)
    cost = max(time.time() - t0 - _AsrLock.waited, 0.001)
    _write(d, a, segs, dur, cost)


PUNCT = _config.model_dir("punct")
_HAS_PUNCT = re.compile(r"[，。？！、；：,.?!;:]")


FIXES = pathlib.Path(__file__).resolve().parent.parent / "config" / "asr_fixes.json"


def fix_terms(segs):
    """电商同音纠错：抖音岳父 → 抖音月付、果补 → 国补、价宝 → 价保……规则在 config/asr_fixes.json。
    """
    try:
        rules = [(re.compile(r["from"]), r["to"])
                 for r in json.loads(FIXES.read_text(encoding="utf-8"))["fixes"]]
    except Exception as e:
        print("  !! 纠错表读不了（%s），这场不纠错" % str(e)[:80], flush=True)
        return 0
    n = 0
    for s in segs:
        src = s.get("asr") or s["text"]
        t = src
        for rx, to in rules:
            t = rx.sub(to, t)
        if t != src:
            s["asr"] = src
            n += 1
        else:
            s.pop("asr", None)
        s["text"] = t
    return n


def punctuate(segs):
    """给每句加一份带标点的阅读文本 punct（2026-09-24 起转写时就加）。
    """
    if not segs:
        return None
    if not (PUNCT / "model.int8.onnx").exists():
        print("  !! 缺标点模型 %s，这场逐字稿不带标点" % PUNCT.name, flush=True)
        return None
    import sherpa_onnx
    p = sherpa_onnx.OfflinePunctuation(sherpa_onnx.OfflinePunctuationConfig(
        model=sherpa_onnx.OfflinePunctuationModelConfig(
            ct_transformer=str(PUNCT / "model.int8.onnx"), num_threads=2)))
    for s in segs:
        t = s["text"]
        s["punct"] = t if _HAS_PUNCT.search(t) else p.add_punctuation(t)
    return "ct-transformer-zh-en-int8"


def _write(d, a, segs, dur, cost):
    if not segs:
        print("  !! 一句都没转写出来 —— 这场多半是无声挂机间，"
              "后续分析会在空文本上空跑，建议移进 data/_lowquality/", flush=True)
    fixed = fix_terms(segs)
    punct = punctuate(segs)
    chars = sum(len(s["text"]) for s in segs)
    speech = sum(s["end"] - s["start"] for s in segs)
    out = {
        "engine": a.engine,
        "model": {"local": a.model, "nano": "funasr-nano-2512-int8"}.get(a.engine, "aliyun"),
        "audio_sec": round(dur, 1), "transcribe_sec": round(cost, 1),
        "segments": len(segs), "chars": chars,
        "speech_sec": round(speech, 1),
        "silence_ratio": round(1 - speech / dur, 3) if dur else 0,
        "chars_per_min": round(chars / (speech / 60), 1) if speech else 0,
        "punct_model": punct,
        "term_fixes": fixed,
        "items": segs,
    }
    p = d / "transcript.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if not getattr(a, "keep_wav", False):
        (d / "audio.wav").unlink(missing_ok=True)

    print("\n转写完成 (耗时 %.0fs，%.1fx 实时)" % (cost, dur / cost if cost else 0))
    print("  %d 句 / %d 字" % (len(segs), chars))
    print("  语速 %.0f 字/分钟（按有效说话时长算）" % out["chars_per_min"])
    print("  静音占比 %.0f%%" % (out["silence_ratio"] * 100))
    print("  -> %s" % p)
    for s in segs[:5]:
        print("    [%6.1f] %s" % (s["start"], s["text"][:46]))


if __name__ == "__main__":
    main()
