#!/usr/bin/env python3
"""模型调用与 JSON 解析的唯一实现。
"""
import json, os, pathlib, re, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / "config" / ".env.local"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.S)


def extract_json(txt):
    """把模型回复里的 JSON 剥出来：去代码块围栏、截首尾大括号、清控制字符。"""
    txt = (txt or "").strip()
    m = _FENCE.search(txt)
    if m:
        txt = m.group(1).strip()
    a, b = txt.find("{"), txt.rfind("}")
    if a >= 0 and b > a:
        txt = txt[a:b + 1]
    return _CTRL.sub("", txt)


def loose_loads(txt):
    """先按标准 JSON 解析；失败再做两处实测常见的修补后重试一次。
    """
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        fixed = re.sub(r'("t"\s*:\s*)(无|未知|none|null|N/A)', r"\g<1>-1", txt)
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
        return json.loads(fixed)


def ask_json(cli, model, messages, max_tokens=3000, retries=2,
             temperature=0.2, json_mode=True, label="模型调用"):
    """调模型并解析 JSON。返回 (结果, token 数)；重试用尽返回 (None, 0)。
    """
    last = None
    for i in range(retries + 1):
        try:
            kw = {"model": model, "messages": messages,
                  "max_tokens": max_tokens, "temperature": temperature}
            if json_mode:
                kw["response_format"] = {"type": "json_object"}
            r = cli.chat.completions.create(**kw)
            return (loose_loads(extract_json(r.choices[0].message.content)),
                    r.usage.total_tokens)
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(2 * (i + 1))
    print("  %s失败：%s" % (label, str(last)[:140]), flush=True)
    return None, 0


def load_env():
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import _config
    _config.load_env()


def conf():
    """模型服务配置。换服务商只改 config/.env.local，任何兼容 OpenAI 接口的服务都行：
      LLM_BASE_URL   接口地址
      LLM_API_KEY    密钥
      LLM_TEXT_MODEL 文本模型（深度语义、切轮、方法论）
      LLM_VL_MODEL   视觉模型（视觉量化、画面分析）
    都不填 = 阿里云百炼（DASHSCOPE_API_KEY、qwen-max、qwen-vl-max）。
    填了 LLM_BASE_URL 而 LLM_VL_MODEL 留空（或写 none）= 这家没有视觉模型，视觉量化和画面分析跳过。
    原来四个脚本各自写死地址和模型名，账号一欠费，想换家服务要改四处。
    换模型后先跑 scripts/bias_check.py：讨好偏差是按模型测的，换了要重测。
    """
    load_env()
    custom = bool(os.environ.get("LLM_BASE_URL"))
    vl = os.environ.get("LLM_VL_MODEL") or ("" if custom else "qwen-vl-max")
    return {"base_url": os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL,
            "key": os.environ.get("LLM_API_KEY") or os.environ.get("DASHSCOPE_API_KEY"),
            "text": os.environ.get("LLM_TEXT_MODEL") or "qwen-max",
            "vl": "" if vl.lower() == "none" else vl}


def text_model():
    return conf()["text"]


def vl_model():
    """视觉模型名；空字符串 = 没配视觉模型，调用方跳过视觉层。"""
    return conf()["vl"]


def client():
    c = conf()
    if not c["key"]:
        raise SystemExit("缺少模型密钥：config/.env.local 里填 LLM_API_KEY 或 DASHSCOPE_API_KEY")
    from openai import OpenAI
    return OpenAI(api_key=c["key"], base_url=c["base_url"])


def probe(timeout=20):
    """模型服务现在能不能用。发一个 1 token 的请求，返回 (能用, 原因)。
    """
    try:
        client().with_options(timeout=timeout, max_retries=0).chat.completions.create(
            model=text_model(), max_tokens=1,
            messages=[{"role": "user", "content": "ping"}])
        return True, ""
    except SystemExit as e:
        return False, str(e)
    except Exception as e:
        return False, (str(e).splitlines() or [""])[0][:160]
