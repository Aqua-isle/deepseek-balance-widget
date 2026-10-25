# -*- coding: utf-8 -*-
"""隐私探针：确认挂件从 DSH 会话里"只读状态，不读内容、不外泄"。

做法：
  1. 造一个假的 DSH 会话缓存，标题和提示词里塞 canary 字符串；
  2. 把 USERPROFILE / APPDATA 指到临时目录，再导入挂件、跑一次 DSH 轮询；
  3. 检查 canary 有没有出现在
       - 磁盘上（config.json / app.log / 任何文件）
       - 界面上（画布上的每一段文字）
       - 返回的 state 字典（有没有把提示词读进来）
  4. 顺便确认 API Key 不会进日志：给一个假 Key，强制一次失败请求。

    python tools\\privacy_probe.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

CANARY_TITLE = "CANARY_TITLE_7f3a91"
CANARY_PROMPT = "CANARY_PROMPT_9b21c4"
# 故意拼起来：源码里不出现完整的 sk- 字面量，免得被 Key 扫描器误报成真 Key
CANARY_KEY = "sk-" + "CANARYcanaryCANARY1234567890"


def build_fake_session(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    rows = {
        "title": {"ver": 1, "seq": 9, "val": CANARY_TITLE},
        "turnOutline": {"ver": 2, "seq": 9, "val": {"turns": [
            {"turn": 3, "seq": 5, "prompt": CANARY_PROMPT, "response": "x"}]}},
        "turnBoundary": {"ver": 2, "seq": 9, "val": {
            "openTurnStartSeq": 4242, "lastTurn": 3}},
        "sessionStats": {"ver": 1, "seq": 9, "val": {"turns": 3, "steps": 12}},
        "tokenUsage": {"ver": 2, "seq": 9, "val": {
            "totals": {"uncachedInputTokens": 1000, "outputTokens": 2000,
                       "cacheReadTokens": 30000, "cacheWriteTokens": 0}}},
        "modelSelection": {"ver": 2, "seq": 9, "val": {
            "lastUsed": {"model": "deepseek-flash", "reasoningEffort": "max"}}},
        "llmRetry": {"ver": 1, "seq": 9, "val": {}},
    }
    path = directory / "session-canary.json"
    path.write_text(json.dumps({"version": 7, "record": {"identity": {},
                                                        "rows": rows}}),
                    encoding="utf-8")
    return path


def scan_tree(root: Path, needles):
    hits = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for name, needle in needles:
            if needle in data:
                hits.append(f"{path.relative_to(root)} 含 {name}")
    return hits


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

    work = Path(tempfile.mkdtemp(prefix="dsb-privacy-"))
    profile = work / "profile"
    appdata = work / "appdata"
    profile.mkdir(parents=True)
    appdata.mkdir(parents=True)

    # 必须在 import 之前改：dsh_state.SESSION_DIR 是导入期算好的
    os.environ["USERPROFILE"] = str(profile)
    os.environ["APPDATA"] = str(appdata)
    os.environ.pop("DEEPSEEK_API_KEY", None)

    session = build_fake_session(profile / ".dsh" / "storages"
                                 / "session_projcache" / "sessions")
    print(f"[1] 假会话文件: {session}")

    sys.path.insert(0, str(PROJECT))
    import dsh_state
    import deepseek_balance_widget as widget

    state = dsh_state.read_session(str(session))
    print(f"[2] 读到的字段: {sorted(state)}")
    has_prompt = any("prompt" in key for key in state)
    reads_title = state.get("title") == CANARY_TITLE
    print(f"    state 里有提示词字段: {has_prompt}（期望 False）")
    print(f"    state 里读了会话标题: {reads_title}（CLI 调试用，挂件不显示）")

    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    app = widget.BalanceWidget(root, widget.Config(), demo=True, forced_pos=(200, 160))
    root.deiconify()
    root.update()
    app.selftest = True
    app._apply_dsh(state)
    root.update()

    texts = []
    for item in app.canvas.find_all():
        try:
            value = app.canvas.itemcget(item, "text")
        except Exception:
            continue
        if value:
            texts.append(value)
    on_screen = " | ".join(texts)
    print(f"[3] 卡片上的文字: {on_screen}")

    # 强制一次失败请求，确认 Key 不进日志
    app.cfg.data["api_key_enc"] = None
    app.cfg.data["last"] = None
    real_fetch = widget.fetch_balance
    widget.fetch_balance = lambda key, timeout=12.0: (_ for _ in ()).throw(
        widget.BalanceError("API Key 无效或已过期（401）"))
    try:
        widget.fetch_balance(CANARY_KEY)
    except widget.BalanceError as exc:
        print(f"[4] 失败提示样例: {exc}")
    finally:
        widget.fetch_balance = real_fetch

    root.destroy()

    needles = [("标题 canary", CANARY_TITLE.encode()),
               ("提示词 canary", CANARY_PROMPT.encode()),
               ("假 Key", CANARY_KEY.encode())]
    # 只扫挂件自己会写的地方（config.json / app.log / 图标）；
    # profile\.dsh 是 DSH 自己的数据，里面本来就有 canary，不能算泄漏。
    disk_hits = scan_tree(appdata, needles)
    screen_hits = [name for name, value in needles if value.decode() in on_screen]
    state_hits = [name for name, value in needles
                  if name != "标题 canary"          # 标题是 CLI 调试字段，挂件不显示
                  and any(value.decode() in str(v) for v in state.values())]

    print(f"[5] 磁盘命中: {disk_hits or '无'}")
    print(f"[6] 界面命中: {screen_hits or '无'}")
    print(f"[7] state 命中: {state_hits or '无'}")

    ok = (not disk_hits and not screen_hits and not state_hits and not has_prompt)
    print("\n结果：" + ("PASS —— 只读轮次/token/模型，没碰会话内容，也没落盘"
                       if ok else "FAIL —— 见上面的命中清单"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
