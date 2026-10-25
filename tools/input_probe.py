#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鼠标输入取证工具：判断"鼠标被抢"是真人操作还是软件注入。

用法:
    python tools/input_probe.py             # 观察 20 秒
    python tools/input_probe.py 60          # 观察 60 秒
    python tools/input_probe.py 20 --test   # 观察的同时注入两个移动作为对照

原理:
    装一个 WH_MOUSE_LL 低层钩子。MSLLHOOKSTRUCT.flags 里的 LLMHF_INJECTED(0x1)
    表示这个事件来自 SendInput / mouse_event / 自动化工具，而不是物理鼠标。
    同时记录事件发生时的前台窗口，方便判断是谁在操作。
"""

import ctypes
import ctypes.wintypes as w
import sys
import threading
import time
from collections import Counter

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, w.WPARAM, w.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
CLICK_MESSAGES = (0x0201, 0x0202, 0x0204, 0x0205)
LLMHF_INJECTED = 0x01


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", w.POINT), ("mouseData", w.DWORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, w.WPARAM, w.LPARAM)


def window_label(hwnd) -> str:
    """返回 '进程名:窗口标题'，用来识别前台是谁。"""
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.GetWindowTextW(hwnd, buf, length + 2)
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    name = ctypes.create_unicode_buffer(260)
    if handle:
        psapi.GetModuleBaseNameW(handle, None, name, 260)
        kernel32.CloseHandle(handle)
    proc = name.value or f"pid{pid.value}"
    return f"{proc}:{buf.value[:40]}"


def probe(seconds: float, inject_test: bool) -> int:
    records = []
    start = time.time()

    def handler(n_code, w_param, l_param):
        if n_code == 0:
            info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            records.append((time.time() - start, w_param, info.pt.x, info.pt.y,
                            info.flags, window_label(user32.GetForegroundWindow())))
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    callback = HOOKPROC(handler)
    hook = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, None, 0)
    if not hook:
        print("安装钩子失败，错误码", ctypes.get_last_error())
        return 2
    print(f"开始观察 {seconds:.0f} 秒……", flush=True)

    if inject_test:
        def inject():
            time.sleep(1.5)
            user32.SetCursorPos(600, 400)
            time.sleep(0.1)
            user32.mouse_event(0x0001, 7, 0, 0, 0)
            time.sleep(0.1)
            user32.mouse_event(0x0001, -7, 0, 0, 0)
        threading.Thread(target=inject, daemon=True).start()

    thread_id = kernel32.GetCurrentThreadId()

    def stop():
        time.sleep(seconds)
        user32.PostThreadMessageW(thread_id, 0x0012, 0, 0)  # WM_QUIT
    threading.Thread(target=stop, daemon=True).start()

    msg = w.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    user32.UnhookWindowsHookEx(hook)

    injected = [r for r in records if r[4] & LLMHF_INJECTED]
    physical = [r for r in records if not r[4] & LLMHF_INJECTED]
    print(f"事件总数 {len(records)}：物理 {len(physical)}，注入 {len(injected)}")
    if injected:
        moves = sum(1 for r in injected if r[1] == WM_MOUSEMOVE)
        clicks = sum(1 for r in injected if r[1] in CLICK_MESSAGES)
        print(f"注入细分：移动 {moves}，点击 {clicks}")
        print("注入时的前台窗口：")
        for label, count in Counter(r[5] for r in injected).most_common(5):
            print(f"  {count:4d} 次  {label}")
        print("最早几笔注入：")
        for r in injected[:8]:
            print(f"  t={r[0]:6.2f}s  msg=0x{r[1]:04x}  pos={r[2]},{r[3]}  fg={r[5]}")
        return 1
    print("没有检测到注入输入——这段时间的鼠标操作都来自真实设备。")
    return 0


if __name__ == "__main__":
    try:  # 让中文在 cmd/PowerShell 里正常显示
        kernel32.SetConsoleOutputCP(65001)
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    secs = float(args[0]) if args else 20.0
    sys.exit(probe(secs, "--test" in sys.argv))
