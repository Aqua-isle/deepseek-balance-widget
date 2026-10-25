# -*- coding: utf-8 -*-
"""DSH 兼容性探针：挂件到底依赖会话缓存的哪些字段，格式变了会怎样。

做法：
  1. 拿一份真实的 session-*.json 当基准；
  2. 逐个"破坏"：删字段、改类型、改版本号、截断、加 BOM、塞超大文件……
  3. 每次调用 dsh_state.read_session()，记录：返回 None / 返回字典 / 抛异常
  4. 最后打印"挂件实际用到的字段"清单

    python tools\\dsh_compat_probe.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import dsh_state  # noqa: E402


def load_real():
    path = dsh_state.latest_path()
    if not path:
        print("找不到真实会话缓存，跳过基准测试")
        return None, None
    with open(path, "r", encoding="utf-8-sig") as handle:
        return path, json.load(handle)


def run_case(work: Path, name: str, payload, encoding="utf-8", raw_bytes=None):
    path = work / f"case-{name}.json"
    if raw_bytes is not None:
        path.write_bytes(raw_bytes)
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding=encoding)
    try:
        state = dsh_state.read_session(str(path))
        if state is None:
            result = "None（当作没数据）"
        else:
            result = (f"dict  open_turn={state['open_turn']} turn_no={state['turn_no']} "
                      f"tokens={sum(state['tokens'].values())} model={state['model']}")
        print(f"  {name:<28} -> {result}")
        return True
    except Exception as exc:                     # 探针只关心有没有异常逃出来
        print(f"  {name:<28} -> 抛异常 {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

    real_path, base = load_real()
    if base is None:
        return 1
    print(f"[1] 基准文件: {real_path}")
    rows = base["record"]["rows"]
    schema_cache = base.get("version")
    schema_record = base["record"].get("identity", {}).get("formatVersion")
    print(f"[2] 版本号: 顶层 version={schema_cache}  record.identity.formatVersion={schema_record}")
    print(f"[3] rows 字段数: {len(rows)}（挂件只读其中 8 个）")

    used = {
        "turnBoundary": ["openTurnStartSeq", "lastTurn"],
        "sessionStats": ["turns", "steps", "llmMs", "toolMs", "lastTurn"],
        "tokenUsage": ["totals.cacheReadTokens", "totals.uncachedInputTokens",
                       "totals.outputTokens"],
        "modelSelection": ["lastUsed.model", "lastUsed.reasoningEffort"],
        "contextPressure": ["pressureTokens", "contextWindow"],
        "timeContext": ["lastMessageTime"],
        "title": ["val"],
        "llmRetry": ["（非空即视为有过重试）"],
    }
    print("[4] 依赖清单:")
    for field, sub in used.items():
        print(f"    {field:<16} {', '.join(sub)}")

    work = Path(tempfile.mkdtemp(prefix="dsh-compat-"))
    print(f"[5] 破坏性测试（工作目录 {work}）:")
    ok = True

    def clone():
        return json.loads(json.dumps(base))

    cases = []
    cases.append(("正常", base, {}, None))
    cases.append(("去掉顶层 record", {k: v for k, v in base.items() if k != "record"}, {}, None))

    p = clone(); p["record"].pop("rows", None)
    cases.append(("去掉 record.rows", p, {}, None))

    p = clone(); p["record"]["rows"] = []
    cases.append(("rows 变成数组", p, {}, None))

    p = clone(); p["version"] = 99; p["record"]["identity"]["formatVersion"] = 99
    cases.append(("版本号改成 99", p, {}, None))

    p = clone(); p["record"]["rows"].pop("turnBoundary", None)
    cases.append(("删掉 turnBoundary", p, {}, None))

    p = clone(); p["record"]["rows"]["turnBoundary"]["val"] = None
    cases.append(("turnBoundary.val=None", p, {}, None))

    p = clone(); p["record"]["rows"]["tokenUsage"]["val"]["totals"]["outputTokens"] = "abc"
    cases.append(("outputTokens 变成字符串", p, {}, None))

    p = clone(); p["record"]["rows"]["modelSelection"]["val"] = "pro"
    cases.append(("modelSelection 变成字符串", p, {}, None))

    raw = json.dumps(base, ensure_ascii=False).encode("utf-8")
    cases.append(("截断（只留前 40%）", None, {}, raw[: int(len(raw) * 0.4)]))
    cases.append(("空文件", None, {}, b""))
    cases.append(("非 JSON", None, {}, b"<!doctype html><html>"))
    cases.append(("UTF-8 BOM", None, {}, b"\xef\xbb\xbf" + raw))
    cases.append(("超大文件（5MB 填充）", None, {}, raw + b" " * (5 * 1024 * 1024)))

    for name, payload, _opts, raw_bytes in cases:
        # 超大文件用空格填充，json 仍合法但预期被大小闸门挡掉
        ok = run_case(work, name, payload, raw_bytes=raw_bytes) and ok

    print(f"\n结果：{'PASS —— 13 种畸形输入都没有异常逃逸' if ok else 'FAIL —— 有输入让解析器抛异常'}")
    shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
