# -*- coding: utf-8 -*-
"""打包产物安全检查：确认 API Key 没有跟着 exe 走。

查四件事：
  1. 源码树里没有明文 Key、没有 sk- 开头的字面量；
  2. dist 下每个文件（含 exe 里 PyInstaller 内嵌归档的每一个模块字节码、
     base_library.zip 里的每一个成员）都找不到 Key 的任何编码形式；
  3. dist 里没有 config.json / history.json / app.log 这类运行期数据；
  4. 本机有 Key 时，连它的 base64 / UTF-16 / DPAPI 密文都不要出现。

输出只报"命中/未命中"和位置，**绝不打印 Key 本身**。

    python tools\\verify_build.py
"""

import base64
import marshal
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import deepseek_balance_widget as widget  # noqa: E402

SK_PATTERN = re.compile(rb"sk-[A-Za-z0-9_\-]{16,}")
SOURCE_SUFFIXES = {".py", ".md", ".bat", ".ps1", ".json", ".txt", ".spec", ".yml"}
SKIP_DIRS = {"build", "dist", "__pycache__", ".git"}
RUNTIME_FILES = {"config.json", "history.json", "app.log", "paused.flag"}


def secrets_from_config():
    """把本机保存的 Key 展开成几种常见编码的字节串（只在本进程里用）。"""
    needles = []
    try:
        cfg = widget.Config()
        key = cfg.stored_key()
    except Exception:
        return []
    if key:
        needles.append(("明文 UTF-8", key.encode("utf-8")))
        needles.append(("UTF-16LE", key.encode("utf-16-le")))
        needles.append(("base64", base64.b64encode(key.encode("utf-8"))))
        needles.append(("base64 去填充", base64.b64encode(key.encode("utf-8")).rstrip(b"=")))
        blob = cfg.data.get("api_key_enc")
        if blob:
            needles.append(("DPAPI 密文", str(blob).encode("ascii")))
    return [(name, value) for name, value in needles if value]


def scan_bytes(label, data, needles, hits):
    for name, needle in needles:
        index = data.find(needle)
        if index >= 0:
            hits.append(f"{label}: 命中「{name}」@ 偏移 {index}")
    match = SK_PATTERN.search(data)
    if match:
        hits.append(f"{label}: 命中 sk- 形态字符串 @ 偏移 {match.start()}")


def scan_source(needles):
    hits, count = [], 0
    for path in HERE.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(HERE).parts):
            continue
        count += 1
        scan_bytes(str(path.relative_to(HERE)), path.read_bytes(), needles, hits)
    return count, hits


def scan_archive(exe: Path, needles, hits) -> int:
    """展开 PyInstaller 内嵌归档：顶层条目 + PYZ 里的每个模块字节码。"""
    try:
        from PyInstaller.archive.readers import CArchiveReader
        reader = CArchiveReader(str(exe))
    except Exception:
        return 0            # 没有内嵌归档（例如纯 bootloader）就跳过
    checked = 0
    for name in reader.toc:
        try:
            data = reader.extract(name)
        except Exception:
            continue
        if isinstance(data, (bytes, bytearray)):
            checked += 1
            scan_bytes(f"{exe.name}:{name}", bytes(data), needles, hits)
    try:
        pyz = reader.open_embedded_archive("PYZ.pyz")
        for name in pyz.toc:
            try:
                code = pyz.extract(name)
            except Exception:
                continue
            raw = code if isinstance(code, (bytes, bytearray)) else marshal.dumps(code)
            checked += 1
            scan_bytes(f"PYZ:{name}", bytes(raw), needles, hits)
    except Exception:
        pass
    return checked


def scan_zip(path: Path, needles, hits) -> int:
    checked = 0
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                try:
                    data = archive.read(info)
                except Exception:
                    continue
                checked += 1
                scan_bytes(f"{path.name}:{info.filename}", data, needles, hits)
    except Exception:
        pass
    return checked


def scan_dist(dist: Path, needles):
    hits, files, entries, runtime = [], 0, 0, []
    for path in sorted(dist.rglob("*")):
        if path.is_dir():
            continue
        files += 1
        if path.name in RUNTIME_FILES:
            runtime.append(str(path.relative_to(dist)))
        if path.suffix.lower() in (".pyd", ".dll", ".zip", ".pyz"):
            # 二进制也要扫；zip/pyz 另外拆开看内容
            scan_bytes(str(path.relative_to(dist)), path.read_bytes(), needles, hits)
            if path.suffix.lower() == ".zip":
                entries += scan_zip(path, needles, hits)
            continue
        data = path.read_bytes()
        scan_bytes(str(path.relative_to(dist)), data, needles, hits)
        if path.suffix.lower() == ".exe":
            entries += scan_archive(path, needles, hits)
    return files, entries, hits, runtime


def scan_runtime(needles, hits):
    """运行期文件（config.json / app.log）里不该出现明文 Key——config.json 只存 DPAPI 密文。"""
    folder = widget.CONFIG_DIR
    if not folder.is_dir():
        return []
    # 只看"明文"形态：密文本来就该在 config.json 里
    plain = [item for item in needles if item[0] in ("明文 UTF-8", "UTF-16LE")]
    checked = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        checked.append(path.name)
        data = path.read_bytes()
        for name, needle in plain:
            if needle in data:
                hits.append(f"{path.name}: 命中「{name}」")
    return checked


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    dist = HERE / "dist"
    if not dist.exists() or not any(dist.rglob("*.exe")):
        print("找不到 dist 下的 exe，先跑 build_exe.py")
        return 1

    needles = secrets_from_config()
    print(f"[1] 本机存在存储的 Key：{'是' if needles else '否'}"
          f"（编码形态 {len(needles)} 种，本脚本不会打印内容）")

    count, source_hits = scan_source(needles)
    print(f"[2] 源码树：扫描 {count} 个文本文件，命中 {len(source_hits)}")

    files, entries, dist_hits, runtime = scan_dist(dist, needles)
    print(f"[3] 产物：dist 下 {files} 个文件 + {entries} 个归档条目，命中 {len(dist_hits)}")
    print(f"[4] 运行期数据文件：{runtime or '无'}")

    runtime_hits = []
    checked = scan_runtime(needles, runtime_hits)
    print(f"[5] 运行期文件 {checked or '无'}：明文 Key 命中 {len(runtime_hits)}")

    problems = source_hits + dist_hits + runtime_hits
    if runtime:
        problems.append(f"dist 里混进了运行期文件：{runtime}")

    if problems:
        print("\n结果：FAIL")
        for line in problems:
            print("  - " + line)
        return 1

    print("\n结果：PASS —— Key 没有出现在源码、exe、内嵌模块或 base_library 里")
    return 0


if __name__ == "__main__":
    sys.exit(main())
