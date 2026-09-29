#!/usr/bin/env python3
"""本地转写模型的检查与下载。模型清单、下载地址、默认目录都在 _config.MODELS。

用法:
  python scripts/setup_models.py                # 列出每个模型的状态
  python scripts/setup_models.py --get nano     # 下载并解压一个（nano / punct）
  python scripts/setup_models.py --get required # 下载所有必需的
  python scripts/setup_models.py --get all      # 全部下载（约 250MB）
"""
import argparse, pathlib, sys, tarfile, urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _config


def mb(n):
    return "%.0f MB" % (n / 1048576)


def status():
    print("转写引擎 ASR_ENGINE = %s\n" % _config.asr_engine())
    for name, m in _config.MODELS.items():
        ok = _config.model_ready(name)
        print("[%s] %-6s %s  %s" % ("OK" if ok else "--", name, mb(m["size"]),
                                   "必需" if m["required"] else "可选"))
        print("       %s" % m["purpose"])
        print("       目录 %s%s\n" % (_config.model_dir(name), "" if ok else "（未就绪）"))
    if _config.asr_engine() == "nano" and not _config.model_ready("nano"):
        print("缺少必需模型，运行：python scripts/setup_models.py --get required")


def download(name):
    m = _config.MODELS[name]
    dest = _config.model_dir(name)
    if _config.model_ready(name):
        print("%s 已就绪：%s" % (name, dest))
        return
    tmp = _config.ROOT / "models" / (m["dir"] + ".tar.bz2.part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    print("下载 %s（%s）\n  %s" % (name, mb(m["size"]), m["url"]), flush=True)
    done = [0]

    def hook(blocks, bs, total):
        n = blocks * bs
        if n - done[0] >= 20 * 1048576 or (total > 0 and n >= total):
            done[0] = n
            print("  %s / %s" % (mb(min(n, total if total > 0 else n)), mb(total if total > 0 else m["size"])),
                  flush=True)

    urllib.request.urlretrieve(m["url"], tmp, hook)
    print("解压到 %s" % dest.parent, flush=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tmp, "r:bz2") as t:
        try:
            t.extractall(dest.parent, filter="data")
        except TypeError:
            t.extractall(dest.parent)
    top = dest.parent / m["dir"]
    if top != dest and top.exists() and not dest.exists():
        top.rename(dest)
    tmp.unlink(missing_ok=True)
    print("%s %s" % (name, "就绪" if _config.model_ready(name) else "解压后仍找不到 " + m["check"]))


def main():
    ap = argparse.ArgumentParser(description="本地转写模型的检查与下载")
    ap.add_argument("--get", help="nano / punct / required / all")
    a = ap.parse_args()
    if not a.get:
        return status()
    names = (list(_config.MODELS) if a.get == "all" else
             [k for k, m in _config.MODELS.items() if m["required"]] if a.get == "required" else [a.get])
    for n in names:
        if n not in _config.MODELS:
            raise SystemExit("没有这个模型：%s（可选 %s）" % (n, " / ".join(_config.MODELS)))
        download(n)


if __name__ == "__main__":
    main()
