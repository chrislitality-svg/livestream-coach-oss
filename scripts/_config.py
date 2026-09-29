#!/usr/bin/env python3
"""项目配置的唯一入口：config/.env.local（不入库）+ 进程环境变量，环境变量优先。
"""
import os, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / "config" / ".env.local"
_loaded = False

MODELS = {
    "nano": {
        "env": "NANO_MODEL_DIR",
        "dir": "sherpa-onnx-sense-voice-funasr-nano-int8-2025-12-17",
        "check": "model.int8.onnx",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
               "sherpa-onnx-sense-voice-funasr-nano-int8-2025-12-17.tar.bz2",
        "size": 187693225,
        "purpose": "主力转写（FunASR-Nano，CPU 约 30~40 倍实时）。ASR_ENGINE=nano 时必需",
        "required": True,
    },
    "punct": {
        "env": "PUNCT_MODEL_DIR",
        "dir": "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8",
        "check": "model.int8.onnx",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/"
               "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2",
        "size": 64717756,
        "purpose": "给逐字稿加标点（只影响阅读，不影响指标）。缺了照常转写，只是没有标点",
        "required": False,
    },
}


def load_env():
    """把 config/.env.local 读进环境变量（已有的环境变量不覆盖）。只读一次。"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    if ENV.exists():
        for ln in ENV.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if "=" in ln and not ln.startswith("#"):
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get(key, default=None):
    load_env()
    v = os.environ.get(key)
    return v if v not in (None, "") else default


def model_dir(name):
    """某个本地模型的目录：配置里写了（绝对或相对项目根）就用配置的，否则 models/<默认目录名>。"""
    m = MODELS[name]
    v = get(m["env"])
    p = pathlib.Path(v) if v else ROOT / "models" / m["dir"]
    return p if p.is_absolute() else ROOT / p


def model_ready(name):
    return (model_dir(name) / MODELS[name]["check"]).exists()


def has_script(name):
    """某个可选模块在不在这个发行版里。各版本带的模块不同，调用方据此跳过，而不是报错。"""
    return (ROOT / "scripts" / name).exists()


def asr_engine():
    """转写引擎：nano（默认，本地 CPU）/ local（faster-whisper，显卡）/ aliyun（云端，要密钥）。
    **全库必须用同一个引擎**，否则时长类指标系统性偏移；换引擎后用 scripts/retranscribe.py 全库重转。
    """
    return get("ASR_ENGINE", "nano")
