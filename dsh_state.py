# -*- coding: utf-8 -*-
"""读取 DSH 本地会话缓存，回答三个问题：在不在跑、跑到第几轮、这一轮花了多少。

只读本地 JSON：不联网、不写 DSH 的任何文件、不调用它的内部接口。
DSH 升级后字段可能变化，所以每个取值都带默认值，读不到就退化，绝不抛异常。

调试：
    python dsh_state.py            # 打印当前状态
    python dsh_state.py --json     # 机器可读
    python dsh_state.py --watch    # 每 2 秒刷新，状态变了才打印
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from datetime import datetime

SESSION_DIR = os.path.join(os.path.expanduser("~"), ".dsh", "storages",
                           "session_projcache", "sessions")

# 官方价（元 / 百万 token，低峰时段）。
# 高峰（工作日 9:00-12:00、14:00-18:00，北京时间）单价翻倍。
PRICES = {
    "flash": {"hit": 0.02, "miss": 1.0, "out": 4.0},
    "pro": {"hit": 0.15, "miss": 4.5, "out": 13.5},
}
PEAK_MULT = 2.0

# tokenUsage.totals 里的三个计费桶
BUCKETS = ("cacheReadTokens", "uncachedInputTokens", "outputTokens")


# ---------------- 底层取值 ----------------
def is_peak(now=None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 12 or 14 <= now.hour < 18


def normalize_model(name):
    """'deepseek-pro' / 'deepseek-flash' → 'pro' / 'flash'，认不出来返回 None。"""
    text = str(name or "").strip().lower()
    if not text:
        return None
    if "pro" in text or "reasoner" in text or "think" in text:
        return "pro"
    if "flash" in text or "chat" in text:
        return "flash"
    return None


def _val(rows, key):
    """rows[key]['val']；任何一层缺失都返回 None。"""
    if not isinstance(rows, dict):
        return None
    row = rows.get(key)
    if isinstance(row, dict):
        return row.get("val")
    return None


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------- 文件 ----------------
def latest_path():
    """最近被写过的会话缓存；没有就返回 None。"""
    try:
        files = glob.glob(os.path.join(SESSION_DIR, "session-*.json"))
    except OSError:
        return None
    best, best_time = None, -1.0
    for path in files:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > best_time:
            best, best_time = path, mtime
    return best


MAX_STATE_BYTES = 4 * 1024 * 1024      # 正常会话缓存 10~100 KB，超过 4 MB 直接不看


def _load_json(path):
    # 轮询跑在界面线程上：先看大小，别让一个被替换成几个 G 的文件把界面卡死
    try:
        if os.path.getsize(path) > MAX_STATE_BYTES:
            return None
    except OSError:
        return None
    # DSH 是整份重写，理论上不会读到半截；真碰上就再等 30ms 重试一次
    for attempt in range(2):
        try:
            # utf-8-sig：容忍带 BOM 的文件（DSH 自己不带，但手工改过就可能带上）
            with open(path, "r", encoding="utf-8-sig") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            if attempt:
                return None
            time.sleep(0.03)
        except Exception:
            return None
    return None


# ---------------- 解析 ----------------
# 见过的缓存版本。DSH 升级后这两个号会变——变了不代表不能读，但字段可能挪位置，
# 所以只记下来、降级用，不因为版本号不合就拒绝解析（那样一体检就全瞎）。
KNOWN_CACHE_VERSION = 7         # 顶层 version
KNOWN_RECORD_VERSION = 4        # record.identity.formatVersion


def _schema(raw):
    """取出 (顶层 version, record.identity.formatVersion)，取不到给 None。"""
    cache = raw.get("version")
    record = raw.get("record")
    identity = record.get("identity") if isinstance(record, dict) else None
    fmt = identity.get("formatVersion") if isinstance(identity, dict) else None
    return _int(cache, -1), _int(fmt, -1)


def read_session(path=None):
    """把一份会话缓存解析成扁平字典；读不到返回 None。"""
    path = path or latest_path()
    if not path:
        return None
    raw = _load_json(path)
    if not isinstance(raw, dict):
        return None
    record = raw.get("record")
    rows = record.get("rows") if isinstance(record, dict) else None
    if not isinstance(rows, dict):
        return None

    cache_version, record_version = _schema(raw)

    usage = _val(rows, "tokenUsage") or {}
    totals = usage.get("totals") if isinstance(usage, dict) else {}
    if not isinstance(totals, dict):
        totals = {}
    last_bucket = usage.get("last") if isinstance(usage, dict) else {}
    if not isinstance(last_bucket, dict):
        last_bucket = {}

    boundary = _val(rows, "turnBoundary") or {}
    if not isinstance(boundary, dict):
        boundary = {}
    stats = _val(rows, "sessionStats") or {}
    if not isinstance(stats, dict):
        stats = {}
    pressure = _val(rows, "contextPressure") or {}
    if not isinstance(pressure, dict):
        pressure = {}
    selection = _val(rows, "modelSelection") or {}
    if not isinstance(selection, dict):
        selection = {}
    used = selection.get("lastUsed") if isinstance(selection, dict) else {}
    if not isinstance(used, dict):
        used = {}

    time_ctx = _val(rows, "timeContext")
    if not isinstance(time_ctx, dict):
        time_ctx = {}

    retry = _val(rows, "llmRetry")
    retry_count = 0
    if isinstance(retry, dict):
        retry_count = sum(1 for _ in retry)
    elif retry:
        retry_count = 1

    title = _val(rows, "title")
    if isinstance(title, dict):
        title = title.get("val") or title.get("text")

    open_turn = boundary.get("openTurnStartSeq")
    try:
        open_turn = int(open_turn) if open_turn is not None else None
    except (TypeError, ValueError):
        open_turn = None

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = time.time()

    return {
        "path": path,
        "name": os.path.basename(path),
        "mtime": mtime,
        "age": max(0.0, time.time() - mtime),
        "title": str(title or ""),
        "open_turn": open_turn,
        "turn_no": _int(boundary.get("lastTurn") or stats.get("lastTurn")),
        "turns": _int(stats.get("turns")),
        "steps": _int(stats.get("steps")),
        "llm_ms": _int(stats.get("llmMs")),
        "tool_ms": _int(stats.get("toolMs")),
        "tokens": {key: _int(totals.get(key)) for key in BUCKETS},
        "last_tokens": last_bucket.get("buckets") if isinstance(
            last_bucket.get("buckets"), dict) else {},
        "model": normalize_model(used.get("model")),
        "model_raw": str(used.get("model") or ""),
        "effort": str(used.get("reasoningEffort") or ""),
        "pressure": _int(pressure.get("pressureTokens")),
        "window": _int(pressure.get("contextWindow")) or 1000000,
        "retry": retry_count,
        "last_message": _num(time_ctx.get("lastMessageTime")) / 1000.0,
        "cache_version": cache_version,
        "record_version": record_version,
        "compat": (cache_version == KNOWN_CACHE_VERSION
                   and record_version == KNOWN_RECORD_VERSION),
    }


# ---------------- 计费 ----------------
def token_cost(totals, model="flash", peak=None) -> float:
    """按 token 桶算钱（元）。peak=None 时按当前时刻判断。"""
    price = PRICES.get(model or "flash") or PRICES["flash"]
    multiplier = PEAK_MULT if (is_peak() if peak is None else peak) else 1.0
    hit = _num((totals or {}).get("cacheReadTokens"))
    miss = _num((totals or {}).get("uncachedInputTokens"))
    out = _num((totals or {}).get("outputTokens"))
    return (hit * price["hit"] + miss * price["miss"] + out * price["out"]) / 1e6 * multiplier


def diff_totals(new, old):
    """两次快照之间的增量（负数当 0）。"""
    new = new or {}
    old = old or {}
    return {key: max(0, _int(new.get(key)) - _int(old.get(key))) for key in BUCKETS}


def session_cost(state, peak=None) -> float:
    if not state:
        return 0.0
    return token_cost(state.get("tokens"), state.get("model") or "flash", peak)


def format_duration(seconds) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {seconds:02d} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes:02d} 分"


# ---------------- 命令行 ----------------
def describe(state):
    if not state:
        return "没有读到 DSH 会话缓存（没开过 DSH？）"
    lines = [
        f"会话  {state['title'] or '(未命名)'}",
        f"文件  {state['name']}  ({state['age']:.0f} 秒前更新)",
        f"状态  {'运行中' if state['open_turn'] else '空闲'}"
        f"  第 {state['turn_no']} 轮 · {state['turns']} 轮 {state['steps']} 步",
        f"模型  {state['model_raw'] or '?'} ({state['model'] or '?'})"
        f"  思考 {state['effort'] or '-'}",
        "token 缓存 {cacheReadTokens:,} · 未命中 {uncachedInputTokens:,}"
        " · 输出 {outputTokens:,}".format(**state["tokens"]),
        f"花费  本会话 ¥{session_cost(state):.2f}"
        f"（{'高峰' if is_peak() else '低峰'}价）",
        f"上下文 {state['pressure']:,} / {state['window']:,}",
        f"耗时  LLM {format_duration(state['llm_ms'] / 1000)}"
        f" · 工具 {format_duration(state['tool_ms'] / 1000)}",
        f"格式  缓存 v{state['cache_version']} / record v{state['record_version']}"
        f"{'' if state['compat'] else '  ← 和认识的版本不同，字段可能对不上'}",
    ]
    if state["retry"]:
        lines.append(f"重试  {state['retry']} 次（可能出过错）")
    return "\n".join(lines)


def main(argv):
    watch = "--watch" in argv
    as_json = "--json" in argv
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not watch:
        state = read_session()
        print(json.dumps(state, ensure_ascii=False, indent=2) if as_json else describe(state))
        return 0

    print("监听中（Ctrl+C 结束）…")
    previous = None
    while True:
        state = read_session()
        stamp = (state or {}).get("mtime"), (state or {}).get("open_turn"), \
            (state or {}).get("tokens"), (state or {}).get("model")
        if stamp != previous:
            previous = stamp
            print(time.strftime("%H:%M:%S"), "运行中" if (state or {}).get("open_turn") else "空闲",
                  f"第{(state or {}).get('turn_no', 0)}轮",
                  f"¥{session_cost(state):.2f}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(0)
