#!/usr/bin/env python3
"""配置向导：大模型服务、转写引擎、模型目录，写进 config/.env.local。

用法:
  python scripts/configure.py              # 交互式，一步步问
  python scripts/configure.py --show       # 看当前配置（密钥打码）
  python scripts/configure.py --test       # 测一下大模型服务通不通
  # 非交互（部署脚本里用）：
  python scripts/configure.py --provider deepseek --key sk-xxx
  python scripts/configure.py --provider custom --base-url http://host:8000/v1 --text-model my-model
  python scripts/configure.py --asr nano
"""
import argparse, os, pathlib, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _config

PROVIDERS = {
    "dashscope": ("阿里云百炼（通义千问）", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "qwen-max", "qwen-vl-max"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat", ""),
    "openai": ("OpenAI", "https://api.openai.com/v1", "gpt-4o", "gpt-4o"),
    "ollama": ("Ollama 本地模型（无需密钥）", "http://localhost:11434/v1", "qwen2.5:14b", "qwen2.5vl:7b"),
    "custom": ("其它兼容 OpenAI 接口的服务", "", "", ""),
    "none": ("暂不配置（只跑规则层）", "", "", ""),
}
ENGINES = {
    "nano": "本地 CPU：FunASR-Nano（默认，无需密钥和显卡）",
    "local": "本地 faster-whisper（建议有 NVIDIA 显卡，需 pip install faster-whisper）",
    "aliyun": "阿里云录音识别（需 DASHSCOPE_API_KEY，需 pip install dashscope）",
}
KEYS = ["LLM_BASE_URL", "LLM_API_KEY", "LLM_TEXT_MODEL", "LLM_VL_MODEL", "ASR_ENGINE",
        "DASHSCOPE_API_KEY", "ASR_MODEL", "NANO_MODEL_DIR", "PUNCT_MODEL_DIR"]
SECRET = ("KEY", "TOKEN", "SECRET")


def read_env():
    if not _config.ENV.exists():
        return [], {}
    lines = _config.ENV.read_text(encoding="utf-8").splitlines()
    vals = {}
    for ln in lines:
        s = ln.strip()
        if "=" in s and not s.startswith("#"):
            k, v = s.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return lines, vals


def write_env(changes):
    """只改动到的键；已有的行原地替换，没有的追加到末尾。"""
    lines, _ = read_env()
    if not lines and (_config.ROOT / "config" / ".env.example").exists():
        lines = (_config.ROOT / "config" / ".env.example").read_text(encoding="utf-8").splitlines()
    left = dict(changes)
    out = []
    for ln in lines:
        s = ln.strip()
        k = s.split("=", 1)[0].strip() if "=" in s and not s.startswith("#") else None
        if k in left:
            out.append("%s=%s" % (k, left.pop(k)))
        else:
            out.append(ln)
    out += ["%s=%s" % kv for kv in left.items()]
    _config.ENV.parent.mkdir(parents=True, exist_ok=True)
    _config.ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n已写入 %s" % _config.ENV)


def mask(k, v):
    if not v:
        return "（未设置）"
    if any(x in k for x in SECRET):
        return v[:4] + "…" + v[-4:] if len(v) > 10 else "****"
    return v


def show():
    _, vals = read_env()
    print("配置文件 %s%s\n" % (_config.ENV, "" if _config.ENV.exists() else "（还不存在）"))
    for k in KEYS:
        v = os.environ.get(k) if k in os.environ and k not in vals else vals.get(k, "")
        print("  %-18s %s" % (k, mask(k, v)))
    print()
    for name in _config.MODELS:
        print("  模型 %-6s %s  %s" % (name, "就绪" if _config.model_ready(name) else "未下载",
                                     _config.model_dir(name)))


def test():
    import _llm
    c = _llm.conf()
    print("接口 %s\n文本模型 %s  视觉模型 %s" % (c["base_url"], c["text"], c["vl"]))
    ok, why = _llm.probe()
    print("大模型服务：%s" % ("可用" if ok else "不可用 —— " + why))
    return ok


def ask(prompt, default=""):
    try:
        v = input("%s%s：" % (prompt, " [%s]" % default if default else "")).strip()
    except EOFError:
        v = ""
    return v or default


def choose(title, opts, default):
    print("\n" + title)
    keys = list(opts)
    for i, k in enumerate(keys, 1):
        print("  %d) %-10s %s" % (i, k, opts[k] if isinstance(opts[k], str) else opts[k][0]))
    while True:
        v = ask("选择编号或名称", default)
        if v in opts:
            return v
        if v.isdigit() and 1 <= int(v) <= len(keys):
            return keys[int(v) - 1]
        print("  没有这个选项")


def llm_changes(provider, base_url=None, key=None, text=None, vl=None, interactive=False):
    if provider == "none":
        return {"LLM_BASE_URL": "", "LLM_API_KEY": "", "LLM_TEXT_MODEL": "", "LLM_VL_MODEL": ""}
    _, url0, text0, vl0 = PROVIDERS[provider]
    _, cur = read_env()
    if interactive:
        base_url = ask("接口地址（OpenAI 兼容的 /v1）", base_url or url0 or cur.get("LLM_BASE_URL", ""))
        if provider != "ollama":
            k0 = cur.get("LLM_API_KEY", "")
            key = ask("API Key%s" % ("（回车保留现有 %s）" % mask("KEY", k0) if k0 else ""), "") or k0
        text = ask("文本模型", text or text0 or cur.get("LLM_TEXT_MODEL", ""))
        vl = ask("视觉模型（没有就留空，视觉量化会跳过）", vl or vl0)
    return {"LLM_BASE_URL": base_url or url0, "LLM_API_KEY": key if key is not None else ("ollama" if provider == "ollama" else ""),
            "LLM_TEXT_MODEL": text or text0, "LLM_VL_MODEL": vl if vl is not None else vl0}


def wizard():
    print("直播话术分析 · 配置向导（回车取方括号里的默认值）")
    _, cur = read_env()
    ch = {}
    p = choose("1/3 大模型服务（用于深度语义、切轮、视觉、方法论；不配也能跑规则层）", PROVIDERS, "dashscope")
    ch.update(llm_changes(p, interactive=True))
    e = choose("2/3 语音转写引擎", ENGINES, cur.get("ASR_ENGINE") or "nano")
    ch["ASR_ENGINE"] = e
    if e == "aliyun" or p == "dashscope":
        k0 = cur.get("DASHSCOPE_API_KEY", "")
        if p == "dashscope" and not k0:
            k0 = ch.get("LLM_API_KEY", "")
        ch["DASHSCOPE_API_KEY"] = ask("DASHSCOPE_API_KEY%s" % ("（回车保留 %s）" % mask("KEY", k0) if k0 else ""), "") or k0
    print("\n3/3 本地模型目录（回车 = 用 models/ 下的默认目录）")
    for name, m in _config.MODELS.items():
        ch[m["env"]] = ask("  %s（%s）" % (m["env"], name), cur.get(m["env"], ""))
    write_env(ch)
    _config._loaded = False
    for k in ch:
        os.environ.pop(k, None)
    if p != "none" and ask("\n现在测试大模型服务？(y/n)", "y").lower().startswith("y"):
        test()
    if e == "nano" and not _config.model_ready("nano"):
        print("\n转写模型还没下载：python scripts/setup_models.py --get required")


def main():
    ap = argparse.ArgumentParser(description="配置向导：写 config/.env.local")
    ap.add_argument("--show", action="store_true", help="查看当前配置")
    ap.add_argument("--test", action="store_true", help="测试大模型服务")
    ap.add_argument("--provider", choices=list(PROVIDERS), help="大模型服务预设")
    ap.add_argument("--base-url")
    ap.add_argument("--key")
    ap.add_argument("--text-model")
    ap.add_argument("--vl-model")
    ap.add_argument("--asr", choices=list(ENGINES), help="转写引擎")
    a = ap.parse_args()
    if a.show:
        return show()
    if a.test:
        return 0 if test() else 1
    if a.provider or a.asr:
        ch = {}
        if a.provider:
            ch.update(llm_changes(a.provider, a.base_url, a.key, a.text_model, a.vl_model))
        if a.asr:
            ch["ASR_ENGINE"] = a.asr
        return write_env(ch)
    wizard()


if __name__ == "__main__":
    sys.exit(main())
