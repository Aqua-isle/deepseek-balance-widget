#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek 余额桌面挂件（Windows / Python 标准库 + tkinter，无第三方依赖）

用法:
    pythonw deepseek_balance_widget.py          # 正常启动（推荐用 pythonw，无黑框）
    python  deepseek_balance_widget.py --demo   # 演示数据，不联网
    python  deepseek_balance_widget.py --pos=1200,80   # 指定初始位置（调试用）

操作:
    左键拖动移动 / 双击立即刷新 / 右键菜单（刷新、改 Key、置顶、开机自启、退出）
    点右下角 "≈" 看详情（预估次数 + 今日花费 + 7 日曲线 + 还能撑几天）

数据来源:
    GET https://api.deepseek.com/user/balance   (Authorization: Bearer <API Key>)
    Key 使用 Windows DPAPI 加密后存放在 %APPDATA%\\DeepSeekBalance\\config.json
    也可用环境变量 DEEPSEEK_API_KEY 覆盖（读取优先级更高）

DSH 联动（可选，见菜单）:
    只读 <用户目录>\\.dsh\\storages\\session_projcache\\sessions 下最新的会话缓存，
    用来显示"运行中/第几轮"、任务完成后提醒、以及跟随 DSH 当前模型切换警戒线。
    不联网、不写 DSH 的任何文件。
"""

import base64
import ctypes
import datetime
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
import winreg
from ctypes import wintypes
from pathlib import Path

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

# 同目录的小模块：读 DSH 会话状态。
# 缺了也不影响余额显示，只是少几个功能——所以用 try 兜住，绝不让挂件起不来。
try:
    import dsh_state
except Exception:
    dsh_state = None
    traceback.print_exc()

APP_NAME = "DeepSeekBalance"
APP_VERSION = "1.0.0"
API_URL = "https://api.deepseek.com/user/balance"
REFRESH_INTERVAL = 300          # 默认轮询间隔（秒）
# 可选的刷新频率：20 秒 / 1 分钟 / 2 分钟 / 5 分钟
REFRESH_CHOICES = (("20 秒", 20), ("1 分钟", 60), ("2 分钟", 120), ("5 分钟", 300))
REFRESH_ALLOWED = {seconds for _label, seconds in REFRESH_CHOICES}
FIRST_RETRY = 60                # 首次失败的退避起点（秒）
LOW_BALANCE_DEFAULT = 20.0      # 低于该金额，金额标红

DSH_ACTIVE_INTERVAL = 3.0       # DSH 正在写会话缓存时的检查间隔（秒）
DSH_IDLE_INTERVAL = 20.0        # 没有活动时的检查间隔（秒）
DSH_QUIET_AFTER = 900           # 缓存 15 分钟没动，就当 DSH 已闲置/关闭
DSH_OPEN_STALE = 6 * 3600       # 一个"进行中"的轮次超过 6 小时，认为状态没落盘
NOTICE_SECONDS = 8              # 任务结束后页脚"✔ 完成"停留多久
TOAST_SECONDS = 10              # 完成提醒浮窗停留多久

# 界面整体缩放（叠加在系统 DPI 缩放之上）
UI_SCALE_DEFAULT = 0.90
UI_SCALE_MIN = 0.60
UI_SCALE_MAX = 1.20
UI_SCALE_CHOICES = (("小", 0.80), ("中", 0.90), ("大", 1.00))

# 卡片版式：以下都是 100% DPI、缩放 1.0 时的逻辑像素
BASE_W = 190
BASE_H = 124
PAD_L = 15
PAD_R = 12
Y_TITLE = 7
Y_AMOUNT = 24
Y_SUB = 68
Y_BANDS = 87
Y_FOOTER = 95
FONT_TITLE = 13
FONT_AMOUNT = 31
FONT_SUB = 12
FONT_FOOTER = 11

# 警戒色带：4 条边界切出 5 档，冷 → 热；只表达"还剩多少钱"
BAND_EDGES_DEFAULT = (200.0, 100.0, 50.0, 20.0)
# 2026-10-25 再调一轮：青更蓝、绿更青、橙更红，照顾绿色弱下的黄/橙/红区分
# 相邻档 ΔE2000（绿色弱/红色弱）最低 13.6，见 tools\contrast_check.py
BAND_COLORS_DEFAULT = ("#3ABFE0", "#46C46E", "#FFE45C", "#FF8420", "#E5484D")
# 历史上用过的默认配色：只有"从没改过颜色"才自动升级，用户自己调过的不动
LEGACY_BAND_COLORS = (
    ("#46C7CC", "#A8D34A", "#F7E27A", "#FF9F1C", "#E5484D"),   # 最初版
    ("#46C7CC", "#7FBF2E", "#FFE45C", "#FF9F1C", "#E5484D"),   # 2026-10-25 上午
)
BAND_IDLE = "#2a3140"      # 无数据时竖条与刻度的灰
BAND_BAR_W = 3
BAND_SEG_H = 3
BAND_SEG_GAP = 3
CHIP_W = 22
CHIP_H = 15

# 右侧边缘的"增值"竖条：竖排两个字，点一下打开充值页
TOPUP_W = 16            # 竖条宽度
TOPUP_TOP = 32          # 竖条上下端（避开右上角的曲线和右下角的 ≈）
TOPUP_BOTTOM = 78
TOPUP_INSET = 4         # 离右边框的距离
TOPUP_FONT = 10
TOPUP_TEXT = "增\n值"
TOPUP_URL = "https://platform.deepseek.com/top_up"

# ---------------- 主题 ----------------
BG = "#1b1f27"
BG_SOFT = "#232935"
BORDER = "#3b4455"
FG = "#e7edf5"
MUTED = "#8b97a8"
# 2026-10-25 提亮：原来的 #6b7789 在深底上只有 3.64:1，小字不达标
DIM = "#7f8ba0"
ACCENT = "#4d6bfe"
# 深色底上当"文字"用的品牌蓝：ACCENT 本体只有 3.81:1，这个提亮版 5.89:1
ACCENT_TEXT = "#7d93ff"
OK = "#3ecf6d"
WARN = "#d6a03c"
ERR = "#f2555a"

CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "app.log"


# ---------------- 日志（不记录 Key） ----------------
def log(msg: str) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 512 * 1024:
            LOG_FILE.write_text("", encoding="utf-8")
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


# ---------------- DPAPI 加解密 ----------------
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _make_blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data) + 1)
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def _crypt32():
    return ctypes.WinDLL("crypt32", use_last_error=True)


_KERNEL32 = None


def _kernel32():
    # 缓存住实例：restype/argtypes 是挂在实例上的，重复创建等于每次都要重设
    global _KERNEL32
    if _KERNEL32 is None:
        _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return _KERNEL32


def dpapi_encrypt(data: bytes) -> bytes:
    in_blob, _keep = _make_blob(data)
    out_blob = _DATA_BLOB()
    ok = _crypt32().CryptProtectData(
        ctypes.byref(in_blob), "DeepSeekBalance", None, None, None, 0x1,
        ctypes.byref(out_blob))
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData 失败")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _kernel32().LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def dpapi_decrypt(data: bytes) -> bytes:
    in_blob, _keep = _make_blob(data)
    out_blob = _DATA_BLOB()
    ok = _crypt32().CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob))
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData 失败")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _kernel32().LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


# ---------------- 配置 ----------------
LAYOUT_VERSION = 2   # 2 = 2026-10-18 收紧版式（卡片更窄、字号更大）
MODEL_PRO_FACTOR = 4.0   # Pro 的单价/警戒线约为 Flash 的 4 倍（实测 3.4~4.3）

# 预估单价（元/次）：Flash 低峰口径，Pro = ×4，高峰 = ×2。
# 口径偏保守——宁可少报次数，别让人以为还很宽裕：
# 大项目 ×1.2、普通 ×1.5、微调 ×2、聊天 ×2（相对最初的一版实测均值）。
ESTIMATE_PRICES = (("复杂项目", 7.2), ("复杂话题/普通项目", 1.5),
                   ("一般话题/项目微调", 0.4), ("聊天", 0.04))


def is_peak_time(now=None) -> bool:
    """北京时间周一至周五 9:00-12:00、14:00-18:00 是高峰，其余（含周末）是低谷。

    法定节假日识别不了——节假日白天会被当成高峰，次数显示偏保守（少一半）。
    """
    now = now or datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 12 or 14 <= now.hour < 18


def format_count(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(int(value))


def _default_config() -> dict:
    return {
        "api_key_enc": None,
        "window": {"x": None, "y": None},
        "topmost": True,
        "locked": False,
        "ignore_injected_drag": True,
        "low_threshold": LOW_BALANCE_DEFAULT,   # 保留旧字段，不再参与着色
        "band_colors": list(BAND_COLORS_DEFAULT),
        "bands": {
            "flash": list(BAND_EDGES_DEFAULT),
            "pro": [round(v * MODEL_PRO_FACTOR, 2) for v in BAND_EDGES_DEFAULT],
        },
        "model": "flash",
        "band_link": True,
        "dsh_status": True,      # 在页脚显示 DSH 任务状态，结束时提醒
        "follow_model": True,    # 警戒线跟随 DSH 当前模型（flash/pro）
        "warning_ack": False,    # 首次运行的安全提示是否已确认
        "refresh_seconds": REFRESH_INTERVAL,   # 自动刷新间隔，四档可选
        "ui_scale": UI_SCALE_DEFAULT,
        "layout": LAYOUT_VERSION,
        "last": None,
    }


class Config:
    def __init__(self):
        self.data = _default_config()
        self.load()

    def load(self):
        try:
            # utf-8-sig：容忍记事本编辑后留下的 BOM
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                merged = _default_config()
                merged.update({k: v for k, v in raw.items() if k in merged})
                self.data = merged
                self._migrate(raw)
                self._upgrade_colors()      # 放在 _migrate 之后：那边可能提前 return
        except FileNotFoundError:
            pass
        except Exception:
            log("读取配置失败：\n" + traceback.format_exc())

    def save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, CONFIG_FILE)
        except Exception:
            log("写入配置失败：\n" + traceback.format_exc())

    def _migrate(self, raw: dict):
        """兼容旧配置：卡片版式换算、单套警戒线 → Flash/Pro 双套。"""
        try:
            # 1) 旧的 bands: {edges, colors} → bands: {flash, pro} + band_colors
            old_bands = raw.get("bands")
            if isinstance(old_bands, dict) and "edges" in old_bands:
                edges = [float(x) for x in old_bands.get("edges") or []]
                if len(edges) == 4:
                    self.data["bands"] = {
                        "flash": edges,
                        "pro": [round(v * MODEL_PRO_FACTOR, 2) for v in edges],
                    }
                colors = old_bands.get("colors")
                if isinstance(colors, list) and len(colors) == 5:
                    self.data["band_colors"] = colors
                self.data["model"] = "flash"
                self.data["band_link"] = True
                self.save()
        except Exception:
            log("迁移警戒线配置失败：\n" + traceback.format_exc())

        try:
            # 2) 旧版式把卡片做宽了，同样的缩放值换算到新基准上才能保持观感不变
            if int(raw.get("layout") or 1) >= LAYOUT_VERSION:
                return
            if "ui_scale" not in raw:
                return
            old = float(raw.get("ui_scale") or UI_SCALE_DEFAULT)
            value = min(UI_SCALE_MAX, max(UI_SCALE_MIN, old * 244.0 / BASE_W))
            # 吸附到最近的一档，保证菜单里能打勾
            value = min(UI_SCALE_CHOICES, key=lambda item: abs(item[1] - value))[1]
            self.data["ui_scale"] = value
            self.data["layout"] = LAYOUT_VERSION
            self.save()
        except Exception:
            log("迁移缩放值失败：\n" + traceback.format_exc())

    def _upgrade_colors(self):
        """老默认配色 → 新默认配色（色弱区分度更好的那一版）。

        用户自己改过的颜色原样保留，不动。
        """
        try:
            colors = self.data.get("band_colors")
            if not isinstance(colors, list) or len(colors) != 5:
                return
            current = [str(c).strip().upper() for c in colors]
            legacy = {tuple(str(c).upper() for c in palette) for palette in LEGACY_BAND_COLORS}
            if tuple(current) in legacy and tuple(current) != tuple(BAND_COLORS_DEFAULT):
                self.data["band_colors"] = list(BAND_COLORS_DEFAULT)
                self.save()
                log("配色升级：换成色弱区分度更好的新默认值")
        except Exception:
            log("升级配色失败：\n" + traceback.format_exc())

    @staticmethod
    def env_key() -> str:
        return (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

    def stored_key(self) -> str:
        blob = self.data.get("api_key_enc")
        if not blob:
            return ""
        try:
            return dpapi_decrypt(base64.b64decode(blob)).decode("utf-8").strip()
        except Exception:
            log("解密已保存的 Key 失败：\n" + traceback.format_exc())
            return ""

    def get_api_key(self) -> str:
        # 界面上明确填过的 Key 优先，环境变量只作为兜底
        return self.stored_key() or self.env_key()

    def has_stored_key(self) -> bool:
        return bool(self.data.get("api_key_enc"))

    def set_api_key(self, key: str):
        if key:
            self.data["api_key_enc"] = base64.b64encode(
                dpapi_encrypt(key.encode("utf-8"))).decode("ascii")
        else:
            self.data["api_key_enc"] = None
        self.save()

    @property
    def low_threshold(self) -> float:
        try:
            return float(self.data.get("low_threshold") or LOW_BALANCE_DEFAULT)
        except (TypeError, ValueError):
            return LOW_BALANCE_DEFAULT

    def band_colors(self):
        """五档颜色。任何一项非法就整体回退默认值，绝不抛异常。"""
        raw = self.data.get("band_colors")
        try:
            values = [str(c).strip() for c in raw]
            if len(values) == 5 and all(_is_hex_color(c) for c in values):
                return values
        except (TypeError, ValueError):
            pass
        return list(BAND_COLORS_DEFAULT)

    @staticmethod
    def _clean_edges(raw):
        try:
            values = [float(x) for x in raw]
        except (TypeError, ValueError):
            return None
        if (len(values) == 4 and all(v >= 0 for v in values)
                and all(values[i] > values[i + 1] for i in range(3))):
            return values
        return None

    # ---- 自动刷新频率 ----
    @property
    def refresh_seconds(self) -> int:
        try:
            value = int(self.data.get("refresh_seconds") or REFRESH_INTERVAL)
        except (TypeError, ValueError):
            return REFRESH_INTERVAL
        return value if value in REFRESH_ALLOWED else REFRESH_INTERVAL

    def set_refresh(self, seconds: int) -> bool:
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            return False
        if seconds not in REFRESH_ALLOWED:
            return False
        self.data["refresh_seconds"] = seconds
        self.save()
        return True

    def default_edges(self, model: str):
        base = BAND_EDGES_DEFAULT
        if model == "pro":
            return [round(v * MODEL_PRO_FACTOR, 2) for v in base]
        return list(base)

    def edges_for(self, model: str):
        model = model if model in ("flash", "pro") else "flash"
        raw = self.data.get("bands")
        if isinstance(raw, dict):
            values = self._clean_edges(raw.get(model))
            if values:
                return values
        return self.default_edges(model)

    @property
    def model(self) -> str:
        value = str(self.data.get("model") or "flash").lower()
        return value if value in ("flash", "pro") else "flash"

    @model.setter
    def model(self, value: str):
        self.data["model"] = value if value in ("flash", "pro") else "flash"

    def active_edges(self):
        return self.edges_for(self.model)

    @property
    def band_link(self) -> bool:
        return bool(self.data.get("band_link", True))

    @band_link.setter
    def band_link(self, value: bool):
        self.data["band_link"] = bool(value)

    def set_edges(self, model: str, values):
        """写入某个模型的四条线；联动开启时另一个模型按 ×4 / ÷4 同步。"""
        cleaned = self._clean_edges(values)
        if not cleaned:
            return False
        bands = self.data.get("bands")
        if not isinstance(bands, dict):
            bands = {}
            self.data["bands"] = bands
        bands[model] = [round(v, 2) for v in cleaned]
        if self.band_link:
            if model == "flash":
                bands["pro"] = [round(v * MODEL_PRO_FACTOR, 2) for v in cleaned]
            else:
                bands["flash"] = [round(v / MODEL_PRO_FACTOR, 2) for v in cleaned]
        self.save()
        return True

    def reset_edges(self, model: str):
        bands = self.data.get("bands")
        if not isinstance(bands, dict):
            bands = {}
            self.data["bands"] = bands
        bands[model] = self.default_edges(model)
        self.save()

    # ---- 预估档位：名称 + 单价 ----
    def estimate_items(self):
        """四个预估档位 [{"name", "price"}]；任何一项不合法就整体回退默认值。"""
        defaults = [{"name": name, "price": price} for name, price in ESTIMATE_PRICES]
        raw = self.data.get("estimate")
        if not isinstance(raw, list) or len(raw) != len(defaults):
            return defaults
        items = []
        for entry in raw:
            if not isinstance(entry, dict):
                return defaults
            name = str(entry.get("name") or "").strip()
            try:
                price = float(entry.get("price"))
            except (TypeError, ValueError):
                return defaults
            if not name or price <= 0:
                return defaults
            items.append({"name": name, "price": price})
        return items

    def set_estimate_items(self, items):
        """写入四个档位；返回 False 表示有非法值，此时什么都不改。"""
        cleaned = []
        for name, price in items:
            name = str(name or "").strip()
            try:
                price = float(price)
            except (TypeError, ValueError):
                return False
            if not name or price <= 0:
                return False
            cleaned.append({"name": name[:12], "price": round(price, 4)})
        if len(cleaned) != len(ESTIMATE_PRICES):
            return False
        self.data["estimate"] = cleaned
        self.save()
        return True


def _is_hex_color(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        return False
    try:
        int(value[1:], 16)
        return True
    except ValueError:
        return False


def _lighten(color: str, factor: float) -> str:
    """把颜色朝白色提亮，用于余额变化的闪烁提示。"""
    try:
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError, TypeError):
        return color
    r += int((255 - r) * factor)
    g += int((255 - g) * factor)
    b += int((255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def rounded_rect(canvas, x0, y0, x1, y1, radius, **options):
    """圆角矩形。Tk 画布没有内建圆角，用一个 smooth 多边形凑出来。"""
    r = max(0.0, min(float(radius), (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    points = [
        x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
        x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
        x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, **options)


# ---------------- 接口 ----------------
class BalanceError(Exception):
    pass


def _parse_balance(payload: dict) -> dict:
    infos = payload.get("balance_infos") or []
    if not isinstance(infos, list) or not infos:
        raise BalanceError("接口没有返回余额信息")
    info = next((i for i in infos if i.get("currency") == "CNY"), infos[0])

    def num(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    return {
        "currency": info.get("currency") or "CNY",
        "total": num(info.get("total_balance")),
        "granted": num(info.get("granted_balance")),
        "topped": num(info.get("topped_up_balance")),
        "available": bool(payload.get("is_available", True)),
        "ts": time.time(),
    }


def fetch_balance(api_key: str, timeout: float = 12.0) -> dict:
    req = urllib.request.Request(API_URL, method="GET")
    req.add_header("Authorization", "Bearer " + api_key)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", f"DeepSeekBalanceWidget/{APP_VERSION}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                detail = str(err.get("message") or "")
            elif isinstance(body, dict):
                detail = str(body.get("message") or "")
        except Exception:
            detail = ""
        msg = {
            400: "请求参数有误",
            401: "API Key 无效或已过期",
            403: "该 Key 没有访问权限",
            404: "余额接口地址不可用",
            429: "请求过于频繁，稍后再试",
        }.get(exc.code)
        if not msg:
            msg = "DeepSeek 服务暂时不可用" if exc.code >= 500 else f"请求失败（HTTP {exc.code}）"
        # 401/403/429 的英文原始信息没有额外价值，只给中文提示
        detail = "" if exc.code in (401, 403, 429) else detail
        raise BalanceError(msg + (f"：{detail[:60]}" if detail else ""))
    except (socket.timeout, TimeoutError):
        raise BalanceError("请求超时，请检查网络")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise BalanceError(f"网络连接失败（{reason}）")
    except OSError as exc:
        raise BalanceError(f"网络错误（{exc}）")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise BalanceError("接口返回了无法解析的数据")
    if not isinstance(payload, dict):
        raise BalanceError("接口返回格式异常")
    return _parse_balance(payload)


def _demo_fetch() -> dict:
    """演示数据：不联网，用于检查界面。"""
    time.sleep(0.5)
    total = 128.42 + (time.time() % 11) * 0.13
    return {
        "currency": "CNY",
        "total": round(total, 2),
        "granted": 20.00,
        "topped": round(total - 20.00, 2),
        "available": True,
        "ts": time.time(),
    }


# ---------------- Windows 杂项 ----------------
def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def system_dpi() -> int:
    try:
        user32 = ctypes.windll.user32
        try:
            return int(user32.GetDpiForSystem())
        except AttributeError:
            hdc = user32.GetDC(0)
            dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
            user32.ReleaseDC(0, hdc)
            return dpi or 96
    except Exception:
        return 96


def _rect_struct():
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    return RECT


class _INPUT_MESSAGE_SOURCE(ctypes.Structure):
    # INPUT_MESSAGE_DEVICE_TYPE deviceType; INPUT_MESSAGE_ORIGIN_ID originId;
    _fields_ = [("deviceType", ctypes.c_int), ("originId", ctypes.c_int)]


def input_is_injected() -> bool:
    """当前正在处理的消息是不是软件注入的（SendInput / mouse_event / 自动化工具）。

    IMO_HARDWARE=1 是真实鼠标键盘，IMO_INJECTED=2 是注入。取不到信息时返回 False，
    宁可放行也不要误伤真实用户。
    """
    try:
        source = _INPUT_MESSAGE_SOURCE()
        if not ctypes.windll.user32.GetCurrentInputMessageSource(ctypes.byref(source)):
            return False
        return source.originId == 2
    except Exception:
        return False


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def _cursor_pos():
    """当前鼠标位置（屏幕坐标）。拖拽缩放靠轮询它，不依赖事件派发。"""
    point = _POINT()
    try:
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y
    except Exception:
        return 0, 0


def _left_button_down() -> bool:
    """左键此刻是不是还按着（GetAsyncKeyState 的最高位）。"""
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
    except Exception:
        return False


def primary_workarea():
    rect = _rect_struct()()
    try:
        if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        pass
    return 0, 0, 1920, 1080


def virtual_screen():
    try:
        m = ctypes.windll.user32.GetSystemMetrics
        return m(76), m(77), m(78), m(79)  # X,Y,CX,CY of virtual screen
    except Exception:
        return 0, 0, 1920, 1080


_MUTEX_HANDLE = None


def _create_mutex(name):
    """创建（或打开）命名互斥量。已经存在时返回 None。"""
    kernel32 = _kernel32()
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.CreateMutexW(None, False, name)
    existed = ctypes.get_last_error() == 183      # ERROR_ALREADY_EXISTS
    if not handle or existed:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return handle


def acquire_single_instance():
    """拿单实例锁。

    句柄必须留在模块全局里：一旦被回收，互斥量就销毁了，等于没锁
    （以前就踩过——第二个实例照样起来了，屏幕上并排两个挂件）。
    """
    global _MUTEX_HANDLE
    if _MUTEX_HANDLE:
        return _MUTEX_HANDLE
    handle = _create_mutex("Local\\DeepSeekBalanceWidget")
    if handle:
        _MUTEX_HANDLE = handle
    return handle


# 已在运行的实例可能只露出"安全提示"那一层（主窗口还没建好），两个标题都算
WIDGET_WINDOW_TITLES = ("DeepSeekBalance", "DeepSeekBalanceWarning")


def find_widget_window() -> int:
    """按窗口标题找已经在跑的挂件（顶层窗口）。"""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _param):
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 2)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 2)
            if buf.value in WIDGET_WINDOW_TITLES:
                found.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(callback, 0)
    return found[0] if found else 0


def raise_existing_widget() -> bool:
    """把已在运行的挂件提到最前（桌面上双击快捷方式时用）。"""
    hwnd = find_widget_window()
    if not hwnd:
        return False
    HWND_TOPMOST = -1
    flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
    ctypes.windll.user32.SetWindowPos(wintypes.HWND(hwnd), HWND_TOPMOST, 0, 0, 0, 0, flags)
    return True


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "DeepSeekBalance"


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def set_autostart(enable: bool, command: str) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
        if enable:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def base_dir() -> str:
    """程序所在目录：源码运行时是脚本目录，打包后是 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def launch_args():
    """启动挂件用的命令行（打包后就是 exe 自己，不带参数）。"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return [exe, os.path.abspath(__file__)]


def launch_command() -> str:
    return " ".join(f'"{part}"' for part in launch_args())


# ---- 跟随 DSH 启动 ----
FOLLOW_VALUE = "DeepSeekBalanceFollower"
FOLLOW_STOP_EVENT = "Local\\DeepSeekBalanceFollowerStop"
FOLLOW_MUTEX_NAME = "Local\\DeepSeekBalanceFollower"
_FOLLOW_MUTEX = None


def follow_script() -> str:
    return os.path.join(base_dir(), "tools", "follow_dsh.py")


def follow_args():
    """启动跟随进程的命令行。

    打包后没有 .py 可跑（目标机器也没装 Python），所以让 exe 自己带 --follow 当看门狗。
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--follow"]
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return [exe, follow_script()]


def follow_command() -> str:
    return " ".join(f'"{part}"' for part in follow_args())


def follow_dsh_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, FOLLOW_VALUE)
        return True
    except OSError:
        return False


def set_follow_dsh(enable: bool) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
        if enable:
            winreg.SetValueEx(key, FOLLOW_VALUE, 0, winreg.REG_SZ, follow_command())
        else:
            try:
                winreg.DeleteValue(key, FOLLOW_VALUE)
            except FileNotFoundError:
                pass


def start_follower() -> bool:
    """立刻起一个跟随进程，不用等下次登录。"""
    if not getattr(sys, "frozen", False) and not os.path.exists(follow_script()):
        return False
    try:
        subprocess.Popen(follow_args(), cwd=base_dir(),
                         creationflags=0x08000000,  # CREATE_NO_WINDOW
                         close_fds=True)
        return True
    except OSError:
        return False


def stop_follower() -> None:
    """通知后台跟随进程退出（它会每 2 秒检查一次这个事件）。"""
    try:
        kernel32 = _kernel32()
        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenEventW(0x0002, False, FOLLOW_STOP_EVENT)  # EVENT_MODIFY_STATE
        if handle:
            kernel32.SetEvent(handle)
            kernel32.CloseHandle(handle)
    except Exception:
        pass


# ---- 计划任务：被"批量杀进程"之后还能自己回来 ----
TASK_NAME = "DeepSeekBalanceFollowerKeepAlive"
TASK_INTERVAL_MINUTES = 5


def task_installed() -> bool:
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            creationflags=0x08000000, capture_output=True, timeout=15)
        return result.returncode == 0
    except Exception:
        return False


def install_task() -> bool:
    """注册一个每 5 分钟跑一次跟随进程的计划任务（当前用户、不需要管理员）。

    跟随进程自带互斥量，已经在跑的时候这次启动会立刻退出，所以代价很小。
    它的价值在于：挂件和跟随进程被一起 taskkill 之后，系统还会把它拉起来。

    用 PowerShell 而不是 schtasks：默认的 schtasks 任务在**电池供电时不启动**，
    还有 72 小时执行上限——笔记本上会悄悄失效。
    """
    args = follow_args()
    exe = args[0]
    extra = " ".join(f'"{item}"' for item in args[1:])
    script = (
        "$ErrorActionPreference='Stop';"
        f"$a=New-ScheduledTaskAction -Execute {_ps_quote(exe)} -Argument {_ps_quote(extra)};"
        "$t=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
        # 不写 -RepetitionDuration：省略才是"无限重复"，写 MaxValue 会超出 XML 取值范围
        f"-RepetitionInterval (New-TimeSpan -Minutes {TASK_INTERVAL_MINUTES});"
        "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable;"
        "$p=New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\\$env:USERNAME\" "
        "-LogonType Interactive -RunLevel Limited;"
        f"Register-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Action $a -Trigger $t "
        "-Settings $s -Principal $p -Force | Out-Null"
    )
    return _powershell(script) == 0


def _powershell(script: str) -> int:
    """跑一段 PowerShell，返回退出码；失败返回 -1。"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            creationflags=0x08000000, capture_output=True, timeout=40)
        return result.returncode
    except Exception:
        return -1


def _ps_quote(text: str) -> str:
    """PowerShell 单引号字符串：内部的 ' 要写成 ''。"""
    return "'" + str(text).replace("'", "''") + "'"


def remove_task() -> bool:
    try:
        result = subprocess.run(
            ["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
            creationflags=0x08000000, capture_output=True, timeout=15)
        return result.returncode == 0
    except Exception:
        return False


# ---- 看门狗本体 ----
class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


def _dsh_running() -> bool:
    """用工具快照扫进程名，不弹权限、不依赖 psutil。"""
    kernel32 = _kernel32()
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)   # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return False
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
    found = False
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if str(entry.szExeFile).lower() == "deepseek harness.exe":
                found = True
                break
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def _kill_widget() -> bool:
    hwnd = find_widget_window()
    if not hwnd:
        return False
    kernel32 = _kernel32()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x0001, False, pid)      # PROCESS_TERMINATE
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 0))
    finally:
        kernel32.CloseHandle(handle)


def _start_widget():
    subprocess.Popen(launch_args(), cwd=base_dir(),
                     creationflags=0x08000000, close_fds=True)


def follow_main(debug: bool = False, restart: bool = False, force: bool = False) -> int:
    """跟随 DSH 的看门狗。

    源码模式由 tools/follow_dsh.py 调进来；打包后由 exe 自己带 --follow 跑。
    计划任务会定期拉起它，所以先看一眼功能开关：关掉「跟随 DSH 启动」时它直接退出，
    免得用户关掉开关后又被计划任务悄悄拉起来。
    """
    global _FOLLOW_MUTEX
    if not force and not follow_dsh_enabled():
        log("跟随进程：跟随功能已关闭，退出")
        return 0

    kernel32 = _kernel32()
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                                      wintypes.LPCWSTR)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    mutex = kernel32.CreateMutexW(None, False, FOLLOW_MUTEX_NAME)
    if not mutex or ctypes.get_last_error() == 183:        # ERROR_ALREADY_EXISTS
        log("跟随进程：已有实例在跑，退出")
        return 0
    _FOLLOW_MUTEX = mutex        # 句柄要留住，否则互斥量当场销毁

    stop_event = kernel32.CreateEventW(None, True, False, FOLLOW_STOP_EVENT)
    pause = CONFIG_DIR / "paused.flag"
    try:
        pause.unlink()           # 清掉上次"手动退出"留下的标记
    except OSError:
        pass
    log(f"跟随进程启动（restart={restart}）")

    seen = False
    miss_count = 0
    while True:
        if stop_event and kernel32.WaitForSingleObject(stop_event, 2000) == 0:
            log("跟随进程：收到停止信号，退出")
            return 0
        if not stop_event:
            time.sleep(2)
        running = _dsh_running()
        if running:
            if restart and not seen:
                if find_widget_window():
                    log("跟随进程：restart 模式，先结束旧挂件")
                    _kill_widget()
                    time.sleep(0.4)
                log("跟随进程：拉起挂件（restart）")
                _start_widget()
            elif not find_widget_window():
                # 用户主动点"退出"会留下标记，这种情况不复活
                if not pause.exists():
                    miss_count += 1
                    # 连看两次（约 4 秒）才动手，避免挂件正在重开时被误判
                    if miss_count >= 2:
                        log(f"跟随进程：发现挂件不在了，重新拉起（{time.strftime('%H:%M:%S')}）")
                        _start_widget()
                        miss_count = 0
                else:
                    miss_count = 0
            else:
                miss_count = 0
        elif seen:
            try:
                pause.unlink()
            except OSError:
                pass
        seen = running


def pick_family(candidates, fallback="Segoe UI"):
    try:
        available = set(tkfont.families())
    except Exception:
        return fallback
    for name in candidates:
        if name in available:
            return name
    return fallback


def set_dark_title_bar(window) -> None:
    """让普通窗口的标题栏变深色（Win10 20H1+ / Win11），失败就保持原样。"""
    try:
        hwnd = int(window.frame(), 16)
    except Exception:
        return
    dwm = ctypes.WinDLL("dwmapi")
    value = ctypes.c_int(1)
    for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE：新版本 20，旧预览版 19
        try:
            hr = dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), attribute,
                                           ctypes.byref(value), ctypes.sizeof(value))
            if hr == 0:
                return
        except Exception:
            return


# ---------------- 图标（纯标准库生成，避免默认的 Tk 羽毛图标） ----------------
def _seg_dist(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    length = vx * vx + vy * vy
    t = 0.0 if length == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / length))
    return ((px - (ax + t * vx)) ** 2 + (py - (ay + t * vy)) ** 2) ** 0.5


def _render_icon(size: int = 64) -> bytes:
    """圆角蓝底 + 白色 ¥，4 倍超采样后缩到 size，返回 PNG 字节。"""
    import struct
    import zlib

    ss = 4
    n = size * ss
    margin = n * 0.02
    half = n / 2 - margin
    radius = n * 0.23
    cx = cy = n / 2

    bg = (0x4D, 0x6B, 0xFE)
    # ¥ 的五段笔画，坐标归一化到 0~1
    strokes = [
        ((0.28, 0.21), (0.50, 0.46)),
        ((0.72, 0.21), (0.50, 0.46)),
        ((0.50, 0.44), (0.50, 0.79)),
        ((0.27, 0.52), (0.73, 0.52)),
        ((0.27, 0.63), (0.73, 0.63)),
    ]
    thickness = n * 0.038

    cover = bytearray(n * n)   # 1 = 在圆角矩形内
    ink = bytearray(n * n)     # 1 = 在 ¥ 笔画上
    for y in range(n):
        py = y + 0.5
        for x in range(n):
            px = x + 0.5
            qx = abs(px - cx) - (half - radius)
            qy = abs(py - cy) - (half - radius)
            d = ((max(qx, 0.0) ** 2 + max(qy, 0.0) ** 2) ** 0.5
                 + min(max(qx, qy), 0.0) - radius)
            if d > 0:
                continue
            idx = y * n + x
            cover[idx] = 1
            if 0.18 * n < px < 0.82 * n and 0.15 * n < py < 0.84 * n:
                for (ax, ay), (bx, by) in strokes:
                    if _seg_dist(px, py, ax * n, ay * n, bx * n, by * n) <= thickness:
                        ink[idx] = 1
                        break

    rows = []
    block = ss * ss
    for y in range(size):
        row = bytearray([0])  # PNG 每行的 filter byte
        for x in range(size):
            covered = 0
            inked = 0
            for dy in range(ss):
                base = (y * ss + dy) * n + x * ss
                for dx in range(ss):
                    idx = base + dx
                    if cover[idx]:
                        covered += 1
                        if ink[idx]:
                            inked += 1
            if covered == 0:
                row += bytes((0, 0, 0, 0))
                continue
            # 底色、字色各占 covered 份，其余是圆角外的透明
            r = (bg[0] * (covered - inked) + 255 * inked) / covered
            g = (bg[1] * (covered - inked) + 255 * inked) / covered
            b = (bg[2] * (covered - inked) + 255 * inked) / covered
            row += bytes((int(r), int(g), int(b), int(covered * 255 / block + 0.5)))
        rows.append(bytes(row))

    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ensure_icon():
    """确保配置目录里有 icon.png / icon.ico，返回两个路径。"""
    import struct

    png_path = CONFIG_DIR / "icon.png"
    ico_path = CONFIG_DIR / "icon.ico"
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not png_path.exists():
            png_path.write_bytes(_render_icon(64))
        if not ico_path.exists():
            png = png_path.read_bytes()
            # ICO 头 + 一个目录项（64x64、32 位、PNG 负载）
            ico_path.write_bytes(
                struct.pack("<HHH", 0, 1, 1)
                + struct.pack("<BBBBHHII", 64, 64, 0, 0, 1, 32, len(png), 22)
                + png)
    except Exception:
        log("生成图标失败：\n" + traceback.format_exc())
    return png_path, ico_path


# ---------------- 主窗口 ----------------
class BalanceWidget:
    def __init__(self, root: tk.Tk, cfg: Config, demo: bool = False, forced_pos=None):
        self.root = root
        self.cfg = cfg
        self.demo = demo
        self.forced_pos = forced_pos

        self.queue = queue.Queue()
        self.fetching = False
        self.last = cfg.data.get("last") if isinstance(cfg.data.get("last"), dict) else None
        self.error = None
        self.retry_delay = FIRST_RETRY
        self.timer_id = None
        self.flash_job = None
        self.alive = True
        self.dialog = None
        self.bands_dialog = None
        self.estimate_dialog = None
        self.locked = bool(cfg.data.get("locked"))
        self._prev_total = self.last.get("total") if self.last else None
        self._edges = cfg.active_edges()
        self._band_colors = cfg.band_colors()
        self._estimate_window = None
        self._estimate_job = None
        self._band = -1

        # ---- DSH 联动 ----
        self._dsh = None            # 最近一次读到的会话状态（None = 没有/太久没动）
        self.selftest = False       # 自检时别往真日志里写假事件
        self._dsh_job = None
        self._dsh_turn = None       # 正在跑的这一轮：起始时间、起始 token、模型
        self._dsh_path = None
        self._dsh_compat_warned = False
        self._resizing = False
        self._resize_origin = None
        self._resize_job = None
        self._notice = None         # 页脚临时消息 (文本, 颜色, 到期时间戳)
        self._notice_job = None
        self._toast = None
        self._toast_job = None

        self.dpi_scale = max(1.0, min(system_dpi() / 96.0, 3.0))
        try:
            self.ui_scale = min(UI_SCALE_MAX, max(UI_SCALE_MIN,
                                                  float(cfg.data.get("ui_scale") or UI_SCALE_DEFAULT)))
        except (TypeError, ValueError):
            self.ui_scale = UI_SCALE_DEFAULT
        self.scale = self.dpi_scale * self.ui_scale
        self._build_fonts()
        self._build_window()
        self._build_menu()

        # 缩放手柄拖到一半时画布会被重建，所以"按下之后"的事件挂到顶层窗口上，
        # 这样画布换了也还收得到（Tk 会从子控件冒泡到 toplevel）。
        self.root.bind("<B1-Motion>", self._on_resize_motion, add="+")
        self.root.bind("<ButtonRelease-1>", self._on_resize_end, add="+")

        self.root.after(150, self._poll_queue)
        self.root.after(400, self.refresh_now)
        self.root.after(1200, self._poll_dsh)
        # 不再自动弹设置窗：它一出现就会抢焦点，任何来源的误输入（自动化脚本、隔壁会话）
        # 都可能落到 Key 输入框里。改用卡片上的"右键设置"提示引导。

    # ---- 尺寸换算 ----
    def px(self, value: float) -> int:
        return int(round(value * self.scale))

    def px_dpi(self, value: float) -> int:
        """只按系统 DPI 缩放，不受挂件大小档位影响（设置窗用）。"""
        return int(round(value * self.dpi_scale))

    def band_index(self, total: float) -> int:
        """当前余额落在第几档（0 = 最安全，4 = 最低）。边界值归入较高一档。"""
        return sum(1 for edge in self._edges if total < edge)

    def reload_bands(self):
        self._edges = self.cfg.active_edges()
        self._band_colors = self.cfg.band_colors()

    # ---- 预估 ----
    def estimate_counts(self, total: float):
        """按当前模型和时段估算余额能做多少次，返回 [(名称, 次数), ...]。"""
        model_mult = MODEL_PRO_FACTOR if self.cfg.model == "pro" else 1.0
        time_mult = 2.0 if is_peak_time() else 1.0
        mult = model_mult * time_mult
        return [(item["name"], total / (item["price"] * mult))
                for item in self.cfg.estimate_items()]

    def estimate_context(self) -> str:
        parts = ["高峰" if is_peak_time() else "低峰",
                 "Pro" if self.cfg.model == "pro" else "Flash"]
        return " · ".join(parts)

    # ---- DSH 联动 ----
    def dsh_enabled(self) -> bool:
        if dsh_state is None:
            return False
        return bool(self.cfg.data.get("dsh_status", True)
                    or self.cfg.data.get("follow_model", True))

    def _poll_dsh(self):
        """看一次 DSH 的会话缓存。读的是本地 JSON，几毫秒，直接在主线程做。"""
        self._dsh_job = None
        if not self.alive or not self.dsh_enabled():
            return
        interval = DSH_IDLE_INTERVAL
        try:
            state = dsh_state.read_session()
        except Exception:
            state = None
            log("读取 DSH 状态失败：\n" + traceback.format_exc())
        if state is not None:
            self._apply_dsh(state)
            age = time.time() - state.get("mtime", 0)
            if age > DSH_QUIET_AFTER:
                interval = DSH_IDLE_INTERVAL
            elif state.get("open_turn") is not None:
                interval = DSH_ACTIVE_INTERVAL
            else:
                interval = 8.0
        if self.alive and self.dsh_enabled():
            self._dsh_job = self.root.after(int(interval * 1000), self._poll_dsh)

    def _apply_dsh(self, state):
        """把一份新的会话状态并进界面：跟随模型、跟踪轮次、结束提醒。"""
        self._dsh = state
        fresh = (time.time() - state.get("mtime", 0)) < DSH_QUIET_AFTER

        # 版本号变了：不罢工（字段还能读到就照用），但日志里留一条，免得"功能悄悄少了一半"
        if not state.get("compat", True) and not self._dsh_compat_warned:
            self._dsh_compat_warned = True
            log(f"DSH 会话缓存格式变了：缓存 v{state.get('cache_version')} / "
                f"record v{state.get('record_version')}"
                f"（认识的是 v{dsh_state.KNOWN_CACHE_VERSION} / "
                f"v{dsh_state.KNOWN_RECORD_VERSION}），按旧字段尽力解析")

        # C：警戒线跟随 DSH 当前模型。只在数据新鲜时跟随，
        # 免得半夜打开挂件时被昨天的 Pro 记录改掉手选的那套。
        if (fresh and self.cfg.data.get("follow_model", True)
                and state.get("model") and state["model"] != self.cfg.model):
            self._follow_model(state["model"])

        # 换了会话（新开一个对话）就不再延续上一轮
        if self._dsh_path and state.get("path") != self._dsh_path:
            self._dsh_turn = None
        self._dsh_path = state.get("path")

        # A：轮次开始 / 结束
        open_turn = state.get("open_turn")
        if open_turn is not None and fresh:
            if self._dsh_turn is None or self._dsh_turn.get("seq") != open_turn:
                self._begin_turn(state, open_turn)
        elif self._dsh_turn is not None:
            self._finish_turn(state)

        self._render()

    def _begin_turn(self, state, seq):
        # 只记时长。单轮花销不再估：轮次结束靠轮询发现，token 增量天然滞后，
        # 算出来的数字时多时少，不如不报。
        self._dsh_turn = {
            "seq": seq,
            "no": state.get("turn_no") or 0,
            "t0": time.time(),
        }

    def _finish_turn(self, state):
        turn, self._dsh_turn = self._dsh_turn, None
        if turn is None:
            return
        elapsed = max(0.0, time.time() - turn["t0"])
        retries = int(state.get("retry") or 0)
        number = turn["no"] or state.get("turn_no") or 0

        if retries:
            text, color = f"⚠ 第{number}轮 · 重试 {retries} 次", WARN
        else:
            text, color = f"✔ 第{number}轮 完成", OK
        self._set_notice(text, color, NOTICE_SECONDS)
        if not self.selftest:
            log(f"DSH 第{number}轮结束：{elapsed:.0f}s，重试 {retries}")

        if self.cfg.data.get("dsh_status", True):
            detail = dsh_state.format_duration(elapsed)
            if retries:
                detail += f" · 重试 {retries} 次"
            title = f"第 {number} 轮完成" if not retries else f"第 {number} 轮有重试"
            self._show_toast(title, detail, color)

    def _follow_model(self, model):
        self.cfg.model = model
        self.cfg.save()
        self.reload_bands()
        if self._dsh is None or self._dsh.get("open_turn") is None:
            self._set_notice(f"警戒线 → {'Pro' if model == 'pro' else 'Flash'}", ACCENT_TEXT, 5)
        if not self.selftest:
            log(f"跟随 DSH 切换模型：{model}")

    def _set_notice(self, text, color, seconds):
        self._notice = (text, color, time.time() + seconds)
        if self._notice_job:
            try:
                self.root.after_cancel(self._notice_job)
            except Exception:
                pass
        self._notice_job = self.root.after(int(seconds * 1000) + 50, self._end_notice)
        self._render()

    def _end_notice(self):
        self._notice_job = None
        self._notice = None
        if self.alive:
            self._render()

    def _status_line(self):
        """页脚优先级：进行中的任务 > 刚结束的结果 > 余额更新时间。"""
        state = self._dsh
        if (state is not None and self.cfg.data.get("dsh_status", True)
                and state.get("open_turn") is not None
                and time.time() - state.get("mtime", 0) < DSH_QUIET_AFTER):
            return f"运行中 · 第{state.get('turn_no') or 0}轮", ACCENT_TEXT
        notice = self._notice
        if notice and time.time() < notice[2]:
            return notice[0], notice[1]
        stamp = float(self.last.get("ts", time.time())) if self.last else time.time()
        return "更新于 " + time.strftime("%H:%M:%S", time.localtime(stamp)), DIM

    # ---- 完成提醒浮窗 ----
    def _show_toast(self, title, detail, color):
        self._hide_toast()
        top = tk.Toplevel(self.root)
        top.title("DeepSeekBalanceToast")
        top.overrideredirect(True)
        top.configure(bg=color)
        top.attributes("-topmost", True)
        frame = tk.Frame(top, bg=BG_SOFT, padx=self.px(11), pady=self.px(8))
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(frame, text=title, bg=BG_SOFT, fg=FG, font=self.f_ui_bold,
                 anchor="w").pack(fill="x")
        tk.Label(frame, text=detail, bg=BG_SOFT, fg=MUTED, font=self.f_ui_small,
                 anchor="w").pack(fill="x", pady=(self.px(3), 0))
        tk.Label(frame, text="点一下关掉", bg=BG_SOFT, fg=DIM,
                 font=self.f_ui_small, anchor="w").pack(fill="x", pady=(self.px(2), 0))

        top.update_idletasks()
        width, height = top.winfo_reqwidth(), top.winfo_reqheight()
        vx, vy, vw, vh = virtual_screen()
        x = self.root.winfo_x() + self.W - width
        y = self.root.winfo_y() + self.H + self.px(8)
        if y + height > vy + vh:
            y = self.root.winfo_y() - height - self.px(8)
        x = max(vx, min(x, vx + vw - width))
        y = max(vy, min(y, vy + vh - height))
        top.geometry(f"+{x}+{y}")

        for widget in (top, frame, *frame.winfo_children()):
            widget.bind("<Button-1>", lambda _e: self._hide_toast())
        self._toast = top
        self._toast_job = self.root.after(int(TOAST_SECONDS * 1000), self._hide_toast)

    def _hide_toast(self):
        if self._toast_job:
            try:
                self.root.after_cancel(self._toast_job)
            except Exception:
                pass
            self._toast_job = None
        window, self._toast = self._toast, None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass

    # ---- 界面 ----
    def _build_fonts(self):
        cjk = pick_family(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"], "Segoe UI")
        num = pick_family(["Segoe UI", "Microsoft YaHei UI"], "Segoe UI")
        self.f_title = tkfont.Font(family=cjk, size=-self.px(FONT_TITLE))
        self.f_amount = tkfont.Font(family=num, size=-self.px(FONT_AMOUNT), weight="bold")
        self.f_sub = tkfont.Font(family=cjk, size=-self.px(FONT_SUB))
        self.f_footer = tkfont.Font(family=cjk, size=-self.px(FONT_FOOTER))
        self.f_ui = tkfont.Font(family=cjk, size=-self.px_dpi(11))
        self.f_ui_bold = tkfont.Font(family=cjk, size=-self.px_dpi(11), weight="bold")
        self.f_ui_small = tkfont.Font(family=cjk, size=-self.px_dpi(9))
        self.f_topup = tkfont.Font(family=cjk, size=-self.px(TOPUP_FONT), weight="bold")
        self._amount_px = self.px(FONT_AMOUNT)

    def _build_window(self):
        root = self.root
        root.title("DeepSeekBalance")
        root.overrideredirect(True)
        root.configure(bg=BG)
        root.attributes("-alpha", 1.0)
        root.attributes("-topmost", bool(self.cfg.data.get("topmost", True)))
        self._build_canvas()
        self._place_window()
        self._render()

    def text_width(self) -> int:
        """一行文字可用的宽度。右侧要给"增值"竖条让位，别让金额压到它身上。"""
        right = self.W - self.px(TOPUP_INSET) - self.px(TOPUP_W) - self.px(5)
        return max(self.px(40), right - self.px(PAD_L))

    def _build_canvas(self):
        """（重新）创建画布和里面的所有元素；换缩放大小时会整体重建一次。"""
        self.W = self.px(BASE_W)
        self.H = self.px(BASE_H)
        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H, bg=BG,
                                highlightthickness=0, bd=0, takefocus=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_rectangle(0, 0, self.W, self.H, fill=BG, outline="")
        self.canvas.create_rectangle(0, 0, self.W - 1, self.H - 1, outline=BORDER)
        self.bar_id = self.canvas.create_rectangle(
            1, 1, self.px(BAND_BAR_W), self.H - 2, fill=BAND_IDLE, outline="")

        self.title_id = self.canvas.create_text(
            self.px(PAD_L), self.px(Y_TITLE), text="DeepSeek 余额", anchor="nw",
            fill=MUTED, font=self.f_title)
        self.dot_id = self.canvas.create_oval(0, 0, 0, 0, fill=DIM, outline="")
        self.amount_id = self.canvas.create_text(
            self.px(PAD_L - 1), self.px(Y_AMOUNT), text="--", anchor="nw", fill=FG,
            font=self.f_amount)
        self.sub_id = self.canvas.create_text(
            self.px(PAD_L), self.px(Y_SUB), text="", anchor="nw", fill=MUTED,
            font=self.f_sub)

        self.band_ids = [
            self.canvas.create_rectangle(0, 0, 0, 0, fill=BAND_IDLE, outline="")
            for _ in range(5)]

        self.footer_id = self.canvas.create_text(
            self.px(PAD_L), self.px(Y_FOOTER), text="", anchor="nw", fill=DIM,
            font=self.f_footer)

        # 右下角的预估 chip：点一下弹出"这些钱还能做多少活"
        chip_x0 = self.W - self.px(PAD_R) - self.px(CHIP_W)
        chip_y0 = self.px(Y_FOOTER) - self.px(1)
        self.chip_bg = self.canvas.create_rectangle(
            chip_x0, chip_y0, chip_x0 + self.px(CHIP_W), chip_y0 + self.px(CHIP_H),
            fill=BG_SOFT, outline=BORDER, tags="chip")
        self.chip_text = self.canvas.create_text(
            chip_x0 + self.px(CHIP_W) / 2, chip_y0 + self.px(CHIP_H) / 2,
            text="≈", fill=MUTED, font=self.f_footer, tags="chip")
        self.canvas.tag_bind("chip", "<Button-1>", self._on_chip)
        self.canvas.tag_bind("chip", "<Enter>",
                             lambda _e: self.canvas.itemconfigure(self.chip_text, fill=FG))
        self.canvas.tag_bind("chip", "<Leave>",
                             lambda _e: self.canvas.itemconfigure(self.chip_text, fill=MUTED))

        # 右边边缘的"增值"竖条：DeepSeek 蓝，两个字竖排，点一下打开充值页
        btn_x1 = self.W - self.px(TOPUP_INSET)
        btn_x0 = btn_x1 - self.px(TOPUP_W)
        btn_y0, btn_y1 = self.px(TOPUP_TOP), self.px(TOPUP_BOTTOM)
        self.topup_bg = rounded_rect(
            self.canvas, btn_x0, btn_y0, btn_x1, btn_y1, self.px(5),
            fill=ACCENT, outline="", tags="topup")
        self.topup_text = self.canvas.create_text(
            (btn_x0 + btn_x1) / 2.0, (btn_y0 + btn_y1) / 2.0, text=TOPUP_TEXT,
            fill="#ffffff", font=self.f_topup, justify="center", tags="topup")
        self.canvas.tag_bind("topup", "<Button-1>", self._on_topup)
        self.canvas.tag_bind("topup", "<Enter>", lambda _e: self._topup_hover(True))
        self.canvas.tag_bind("topup", "<Leave>", lambda _e: self._topup_hover(False))

        # 右下角的缩放手柄：拖它按比例改大小（0.60~1.20），不用再进菜单选档。
        # 用 DeepSeek 蓝，和「增值」同色系，比灰的更显眼；位置要避开上面的 ≈ 按钮。
        gx1, gy1 = self.W - self.px(3), self.H - self.px(3)
        gx0, gy0 = gx1 - self.px(11), gy1 - self.px(11)
        self.canvas.create_rectangle(gx0, gy0, gx1, gy1, fill=BG, outline="", tags="grip")
        for index in range(3):
            span = self.px(4 + index * 3)
            self.canvas.create_line(gx1 - span, gy1 - self.px(1), gx1 - self.px(1),
                                    gy1 - span, fill=ACCENT,
                                    width=max(2, self.px(1)), tags=("grip", "gripline"))
        self.canvas.tag_bind("grip", "<Button-1>", self._on_grip_press)
        self.canvas.tag_bind("grip", "<Enter>", lambda _e: self._grip_hover(True))
        self.canvas.tag_bind("grip", "<Leave>", lambda _e: self._grip_hover(False))

        self._place_bands()
        self._bind_widget(self.canvas)

    # ---- 右下角手柄拖拽缩放 ----
    def _grip_hover(self, on: bool):
        try:
            self.canvas.itemconfigure(
                "gripline", fill=_lighten(ACCENT, 0.30) if on else ACCENT)
            self.canvas.configure(cursor="size_nw_se" if on else "")
        except tk.TclError:
            pass
    def _on_grip_press(self, event):
        if self.locked:
            return "break"
        if self.cfg.data.get("ignore_injected_drag", True) and input_is_injected():
            return "break"
        self._resizing = True
        self._resize_origin = (_cursor_pos()[0], self.W)
        try:
            self.canvas.configure(cursor="size_nw_se")
        except tk.TclError:
            pass
        # 主路径还是鼠标事件；但这个 60Hz 的轮询负责兜底：
        # 鼠标拖出窗口后 Windows 就不再派发 <B1-Motion>，只靠事件会"拖到一半卡住"。
        self._start_resize_tick()
        return "break"     # 别让这次按下变成"拖动窗口"

    def _start_resize_tick(self):
        if self._resize_job:
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.root.after(16, self._resize_tick)

    def _resize_tick(self):
        self._resize_job = None
        if not self._resizing or not self.alive:
            return
        if not _left_button_down():      # 松开（哪怕松开事件丢在窗口外）就结束
            self._finish_resize()
            return
        self._apply_resize(_cursor_pos()[0])
        if self._resizing:
            self._resize_job = self.root.after(16, self._resize_tick)

    def _on_resize_motion(self, event):
        if not self._resizing:
            return
        if not _left_button_down():      # release 事件没收到时的自愈
            self._finish_resize()
            return
        self._apply_resize(event.x_root)

    def _apply_resize(self, x_root):
        origin = self._resize_origin
        base = BASE_W * self.dpi_scale
        if not origin or base <= 0:
            return
        target = origin[1] + (x_root - origin[0])
        scale = max(UI_SCALE_MIN, min(UI_SCALE_MAX, target / base))
        # 每 0.8% 重建一次画布：再细就纯属浪费，再粗就一格一格跳
        if abs(scale - self.ui_scale) >= 0.008:
            self.set_ui_scale(scale)

    def _finish_resize(self):
        self._resizing = False
        self._resize_origin = None
        if self._resize_job:
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:
                pass
            self._resize_job = None
        try:
            self.canvas.configure(cursor="")
        except tk.TclError:
            pass
        if self.alive:
            self._save_position()

    def _on_resize_end(self, _event=None):
        if self._resizing:
            self._finish_resize()

    def _on_chip(self, _event):
        self._toggle_estimate()
        return "break"     # 别让这次点击冒泡成拖动

    def _on_topup(self, _event=None):
        """点"增值"打开官方充值页。浏览器会抢焦点，但这是用户主动点的。"""
        try:
            webbrowser.open(TOPUP_URL)
        except Exception:
            log("打开充值页失败：\n" + traceback.format_exc())
        return "break"     # 同上：别冒泡成拖动

    def _topup_hover(self, on: bool):
        try:
            self.canvas.itemconfigure(self.topup_bg,
                                      fill=_lighten(ACCENT, 0.20) if on else ACCENT)
            self.canvas.configure(cursor="hand2" if on else "")
        except tk.TclError:
            pass

    def _toggle_estimate(self):
        if self._estimate_window is not None:
            self._hide_estimate()
            return
        self._show_estimate()

    def _show_estimate(self):
        """详情浮层：按当前余额折算的次数 + DSH 本会话花了多少。"""
        total = float(self.last.get("total", 0.0)) if self.last else 0.0
        estimates = self.estimate_counts(total)
        value_color = self._band_colors[max(0, self._band)] if self._band >= 0 else FG

        top = tk.Toplevel(self.root)
        top.title("DeepSeekBalanceDetail")     # 给窗口起个独特名字：截图/调试时能精确定位
        top.overrideredirect(True)
        top.configure(bg=BORDER)
        top.attributes("-topmost", True)
        frame = tk.Frame(top, bg=BG, padx=self.px(11), pady=self.px(8))
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        frame.columnconfigure(1, weight=1)

        def separator(row):
            tk.Frame(frame, height=1, bg=BORDER).grid(
                row=row, column=0, columnspan=2, sticky="ew",
                pady=(self.px(5), self.px(5)))

        def line(row, name, value, color=FG, bold=True):
            tk.Label(frame, text=name, bg=BG, fg=MUTED, font=self.f_ui_small,
                     anchor="w").grid(row=row, column=0, sticky="w",
                                      padx=(0, self.px(16)))
            tk.Label(frame, text=value, bg=BG, fg=color,
                     font=self.f_ui_bold if bold else self.f_ui_small,
                     anchor="e").grid(row=row, column=1, sticky="e")

        tk.Label(frame, text=self.estimate_context(), bg=BG, fg=MUTED,
                 font=self.f_ui_small, anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, self.px(4)))

        row = 1
        for name, count in estimates:
            line(row, name, format_count(count), value_color)
            row += 1

        # DSH 本会话花费
        state = self._dsh
        if dsh_state is not None and state and \
                time.time() - state.get("mtime", 0) < DSH_QUIET_AFTER:
            separator(row)
            row += 1
            status = "运行中" if state.get("open_turn") is not None else "空闲"
            cost = dsh_state.session_cost(state)
            line(row, "DSH", f"第{state.get('turn_no') or 0}轮 {status} · ¥{cost:.2f}",
                 MUTED, bold=False)
            row += 1

        top.update_idletasks()
        width, height = top.winfo_reqwidth(), top.winfo_reqheight()
        vx, vy, vw, vh = virtual_screen()
        x = min(self.root.winfo_x() + self.W - width, vx + vw - width)
        y = self.root.winfo_y() - height - self.px(6)      # 优先弹在卡片上方
        if y < vy:
            y = self.root.winfo_y() + self.H + self.px(6)  # 上面放不下就放下方
        x = max(vx, x)
        y = max(vy, min(y, vy + vh - height))
        top.geometry(f"+{x}+{y}")

        self._estimate_window = top
        self._estimate_job = self.root.after(20000, self._hide_estimate)

    def _hide_estimate(self):
        if self._estimate_job:
            try:
                self.root.after_cancel(self._estimate_job)
            except Exception:
                pass
            self._estimate_job = None
        window, self._estimate_window = self._estimate_window, None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass

    def _place_bands(self):
        """五段刻度：与文字区左右对齐，段间留 3px。"""
        x0 = self.px(PAD_L)
        x1 = self.W - self.px(PAD_R)
        gap = self.px(BAND_SEG_GAP)
        seg = max(4.0, (x1 - x0 - gap * 4) / 5.0)
        y0 = self.px(Y_BANDS)
        y1 = y0 + max(2, self.px(BAND_SEG_H))
        for index, item in enumerate(self.band_ids):
            left = x0 + index * (seg + gap)
            self.canvas.coords(item, round(left), y0, round(left + seg), y1)

    def _place_window(self):
        pos = self.forced_pos
        if pos is None:
            saved = self.cfg.data.get("window") or {}
            x, y = saved.get("x"), saved.get("y")
            if isinstance(x, int) and isinstance(y, int):
                pos = (x, y)
        if pos is None:
            wx, wy, ww, _wh = primary_workarea()
            pos = (wx + ww - self.W - self.px(40), wy + self.px(48))
        self._place_at(pos[0], pos[1])

    def _place_at(self, x, y):
        vx, vy, vw, vh = virtual_screen()
        x = max(vx, min(int(x), vx + vw - self.W))
        y = max(vy, min(int(y), vy + vh - self.H))
        self.pos = (x, y)
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

    def set_ui_scale(self, value: float):
        """即时换挡：重建字体和画布，保持左上角不动。"""
        value = round(float(value), 2)
        if abs(value - self.ui_scale) < 0.005:
            return
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.ui_scale = min(UI_SCALE_MAX, max(UI_SCALE_MIN, value))
        self.scale = self.dpi_scale * self.ui_scale
        self.cfg.data["ui_scale"] = self.ui_scale
        self.cfg.save()

        old = self.canvas
        self._hide_estimate()      # 画布重建后 chip 是新对象，浮层先收掉
        self._build_fonts()
        self._build_canvas()
        self._place_at(x, y)
        try:
            old.destroy()
        except tk.TclError:
            pass
        self._render()

    def set_refresh(self, seconds: int):
        """切换自动刷新频率；不在拉取中就立刻按新间隔重排下一次。"""
        if not self.cfg.set_refresh(seconds):
            return
        if not self.fetching:
            self._schedule(seconds)

    def _place_dot(self, color: str):
        cx, cy = self.W - self.px(14), self.px(15)
        r = max(3, self.px(3.5))
        self.canvas.coords(self.dot_id, cx - r, cy - r, cx + r, cy + r)
        self.canvas.itemconfigure(self.dot_id, fill=color, state="normal")

    @staticmethod
    def _symbol(currency: str) -> str:
        return "¥" if (currency or "CNY").upper() == "CNY" else "$"

    def _money(self, value: float, currency: str) -> str:
        return f"{self._symbol(currency)}{value:,.2f}"

    def _set_amount(self, text: str, color: str):
        size = self._amount_px
        max_w = self.text_width()
        self.f_amount.configure(size=-size)
        while size > self.px(15) and self.f_amount.measure(text) > max_w:
            size -= self.px(2)
            self.f_amount.configure(size=-size)
        self.canvas.itemconfigure(self.amount_id, text=text, fill=color)

    def _ellipsize(self, text: str, font: tkfont.Font, max_w: int) -> str:
        if font.measure(text) <= max_w:
            return text
        while text and font.measure(text + "…") > max_w:
            text = text[:-1]
        return text + "…"

    def _render(self):
        if not self.alive:
            return
        last, fetching, error = self.last, self.fetching, self.error
        max_w = self.text_width()

        # 水位档位：只由余额决定；无数据时没有颜色
        band = -1
        if last is not None:
            if last.get("available", True):
                band = self.band_index(float(last.get("total", 0.0)))
            else:
                band = 4        # 余额不足 / API 停用，强制最低档
        self._band = band

        if last is None:
            amount, amount_color = "--", MUTED
            sub = "尚未获取余额"
        else:
            amount = self._money(float(last.get("total", 0.0)), last.get("currency", "CNY"))
            if error:
                amount_color = DIM      # 数据过期：压暗，颜色让位给"这数不可信"
            elif band >= 0:
                # 金额跟着档位走；第 5 档用更亮的红，保证大字号可读
                amount_color = ERR if band == 4 else self._band_colors[band]
            else:
                amount_color = FG
            # 副行不重复货币符号（上面的大数字已经给出单位），否则 3 位数余额就会被截断。
            # 赠金/充值哪一项为 0 就不显示那一项——赠金用完之后没必要一直挂个 0.00 占地方。
            granted = float(last.get("granted", 0.0))
            topped = float(last.get("topped", 0.0))
            parts = []
            if granted > 0.004:
                parts.append(f"赠金 {granted:,.2f}")
            if topped > 0.004:
                parts.append(f"充值 {topped:,.2f}")
            sub = " · ".join(parts)

        if fetching:
            dot, footer, footer_color = ACCENT, "正在刷新…", MUTED
        elif error and last is None:
            dot, footer, footer_color = ERR, error, ERR
        elif error:
            dot, footer, footer_color = WARN, error, WARN
        elif last is None:
            dot = DIM
            footer = "未保存 API Key · 右键设置" if not self.cfg.get_api_key() else "尚未获取"
            footer_color = MUTED
        elif not last.get("available", True):
            dot, footer, footer_color = ERR, "余额不足，API 调用已停用", ERR
        else:
            dot = OK
            footer, footer_color = self._status_line()

        self._set_amount(amount, amount_color)
        self.canvas.itemconfigure(
            self.sub_id, text=self._ellipsize(sub, self.f_sub, max_w), fill=MUTED)
        self.canvas.itemconfigure(
            self.footer_id,
            text=self._ellipsize(footer, self.f_footer,
                                 max_w - self.px(CHIP_W + 8)),
            fill=footer_color)
        self._place_dot(dot)

        # 色带：竖条 + 五段刻度。刻度改成"油量表"——余额越高亮得越多，
        # 亮着的段统一用当前档位的颜色；余额往下掉时从**左边**开始熄灭，右侧始终是"剩下的量"。
        self.canvas.itemconfigure(
            self.bar_id, fill=BAND_IDLE if band < 0 else self._band_colors[band])
        lit = 0 if band < 0 else max(0, len(self.band_ids) - band)
        start = len(self.band_ids) - lit        # 右对齐：只亮最右边这几段
        for index, item in enumerate(self.band_ids):
            self.canvas.itemconfigure(
                item, fill=self._band_colors[band] if index >= start else BAND_IDLE)

    # ---- 菜单 ----
    def _build_menu(self):
        menu_kw = dict(tearoff=0, bd=0, activebackground=ACCENT,
                       activeforeground="#ffffff", font=self.f_ui,
                       bg=BG_SOFT, fg=FG, activeborderwidth=0, relief="flat")
        self.menu = tk.Menu(self.root, **menu_kw)
        self._idx = {}          # 菜单项编号按 key 记，避免以后插一项就全错位

        def command(key, label, handler):
            self.menu.add_command(label=label, command=handler)
            self._idx[key] = self.menu.index("end")

        command("refresh", "立即刷新", lambda: self.refresh_now(manual=True))
        self.refresh_menu = tk.Menu(self.menu, **menu_kw)
        for label, seconds in REFRESH_CHOICES:
            self.refresh_menu.add_command(
                label=label, command=lambda s=seconds: self.set_refresh(s))
        self.menu.add_cascade(label="刷新频率", menu=self.refresh_menu)
        self._idx["refresh_freq"] = self.menu.index("end")
        command("key", "修改 API Key…", self.open_settings)
        self.menu.add_separator()
        command("topmost", "总在最前", self._toggle_topmost)
        command("autostart", "开机自启", self._toggle_autostart)
        command("follow_dsh", "跟随 DSH 启动", self._toggle_follow_dsh)
        command("keepalive", "强制关闭后自动恢复", self._toggle_keepalive)
        command("dsh_status", "显示 DSH 状态", self._toggle_dsh_status)
        command("follow_model", "跟随 DSH 模型", self._toggle_follow_model)
        command("lock", "锁定位置", self._toggle_lock)
        command("script_drag", "允许脚本拖动", self._toggle_script_drag)

        self.size_menu = tk.Menu(self.menu, **menu_kw)
        self.size_menu.add_command(label="拖右下角可自由缩放", state="disabled")
        self.size_menu.add_separator()
        self._size_base = self.size_menu.index("end") + 1     # 三档快捷项从这里开始
        for label, value in UI_SCALE_CHOICES:
            self.size_menu.add_command(
                label=label, command=lambda v=value: self.set_ui_scale(v))
        self.menu.add_cascade(label="大小", menu=self.size_menu)
        command("bands", "警戒线设置…", self.open_bands)
        command("estimate", "预估档位设置…", self.open_estimate)

        self.menu.add_separator()
        command("quit", "退出", self.quit_app)

    @staticmethod
    def _check_label(text: str, checked: bool) -> str:
        # 勾号放右边：切换状态时文字不位移，也不会出现 Tk 深色菜单里看不清的黑勾
        return text + ("   ✓" if checked else "")

    def _sync_menu(self):
        """把勾选状态刷到菜单项上。弹菜单前调用；截图调试时也单独用。"""
        topmost = bool(self.root.attributes("-topmost"))
        self.menu.entryconfigure(self._idx["refresh"],
                                 state="disabled" if self.fetching else "normal")
        current = self.cfg.refresh_seconds
        for index, (label, seconds) in enumerate(REFRESH_CHOICES):
            self.refresh_menu.entryconfigure(
                index, label=self._check_label(label, seconds == current))
        self.menu.entryconfigure(self._idx["topmost"],
                                 label=self._check_label("总在最前", topmost))
        self.menu.entryconfigure(self._idx["autostart"],
                                 label=self._check_label("开机自启", autostart_enabled()))
        self.menu.entryconfigure(self._idx["follow_dsh"],
                                 label=self._check_label("跟随 DSH 启动", follow_dsh_enabled()))
        self.menu.entryconfigure(
            self._idx["keepalive"],
            label=self._check_label("强制关闭后自动恢复", task_installed()),
            state="normal" if follow_dsh_enabled() else "disabled")
        self.menu.entryconfigure(
            self._idx["dsh_status"],
            label=self._check_label("显示 DSH 状态",
                                    bool(self.cfg.data.get("dsh_status", True))),
            state="normal" if dsh_state is not None else "disabled")
        self.menu.entryconfigure(
            self._idx["follow_model"],
            label=self._check_label("跟随 DSH 模型",
                                    bool(self.cfg.data.get("follow_model", True))),
            state="normal" if dsh_state is not None else "disabled")
        self.menu.entryconfigure(self._idx["lock"],
                                 label=self._check_label("锁定位置", self.locked))
        self.menu.entryconfigure(
            self._idx["script_drag"],
            label=self._check_label("允许脚本拖动",
                                    not bool(self.cfg.data.get("ignore_injected_drag", True))))
        for index, (label, value) in enumerate(UI_SCALE_CHOICES):
            self.size_menu.entryconfigure(
                self._size_base + index,
                label=self._check_label(label, abs(value - self.ui_scale) < 0.005))

    def _popup_menu(self, event):
        self._sync_menu()
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _toggle_topmost(self):
        value = not bool(self.root.attributes("-topmost"))
        self.root.attributes("-topmost", value)
        self.cfg.data["topmost"] = value
        self.cfg.save()

    def _toggle_lock(self):
        self.locked = not self.locked
        self.cfg.data["locked"] = self.locked
        self.cfg.save()

    def _toggle_script_drag(self):
        allow = not bool(self.cfg.data.get("ignore_injected_drag", True))
        self.cfg.data["ignore_injected_drag"] = not allow
        self.cfg.save()

    def _toggle_dsh_status(self):
        value = not bool(self.cfg.data.get("dsh_status", True))
        self.cfg.data["dsh_status"] = value
        self.cfg.save()
        if not value:
            if self._notice_job:
                try:
                    self.root.after_cancel(self._notice_job)
                except Exception:
                    pass
                self._notice_job = None
            self._notice = None
            self._hide_toast()
        self._render()
        self._kick_dsh()

    def _toggle_follow_model(self):
        value = not bool(self.cfg.data.get("follow_model", True))
        self.cfg.data["follow_model"] = value
        self.cfg.save()
        self._kick_dsh()

    def _kick_dsh(self):
        """开关改动后立刻重排一次轮询，不用等下一个周期。"""
        if not self.alive:
            return
        if self.dsh_enabled():
            if self._dsh_job is None:
                self._dsh_job = self.root.after(200, self._poll_dsh)
        elif self._dsh_job is not None:
            try:
                self.root.after_cancel(self._dsh_job)
            except Exception:
                pass
            self._dsh_job = None

    def _toggle_follow_dsh(self):
        want = not follow_dsh_enabled()
        try:
            set_follow_dsh(want)
        except OSError as exc:
            messagebox.showerror("跟随 DSH 启动", f"写入注册表失败：{exc}", parent=self.root)
            return
        if want:
            start_follower()
        else:
            stop_follower()
            # 关掉跟随之后，"计划任务定期拉起跟随进程"就没有意义了，一并撤掉
            if task_installed():
                remove_task()

    def _toggle_keepalive(self):
        """计划任务：即使挂件和跟随进程被一起强杀，5 分钟内也能自己回来。"""
        want = not task_installed()
        if want and not follow_dsh_enabled():
            messagebox.showinfo("强制关闭后自动恢复",
                                "先打开「跟随 DSH 启动」，这个守护才有意义。",
                                parent=self.root)
            return
        ok = install_task() if want else remove_task()
        if not ok:
            messagebox.showerror("强制关闭后自动恢复",
                                 "写入计划任务失败（可能被组策略限制）。",
                                 parent=self.root)
            return
        if want:
            start_follower()

    def _toggle_autostart(self):
        want = not autostart_enabled()
        try:
            set_autostart(want, launch_command())
            self.cfg.data["autostart"] = want
            self.cfg.save()
        except OSError as exc:
            messagebox.showerror("开机自启", f"写入注册表失败：{exc}", parent=self.root)

    # ---- 拖动 ----
    def _bind_widget(self, widget):
        # 只绑在画布上：事件本来就会冒泡到顶层窗口，两边都绑会让右键菜单弹两遍
        widget.bind("<Button-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_move)
        widget.bind("<ButtonRelease-1>", self._drag_end)
        widget.bind("<Double-Button-1>", lambda e: self.refresh_now(manual=True))
        widget.bind("<Button-3>", self._popup_menu)

    def _drag_start(self, event):
        # 三重闸门，防止被"别人"的输入推走：
        # 1) 用户手动锁定位置；2) 软件注入的点击（自动化脚本/SendInput）；3) 正常拖动
        if self._resizing:
            self._drag_offset = None
            self._dragged = False
            return
        if self.locked:
            self._drag_offset = None
            self._dragged = False
            return
        if self.cfg.data.get("ignore_injected_drag", True) and input_is_injected():
            self._drag_offset = None
            self._dragged = False
            return
        self._drag_offset = (event.x_root - self.root.winfo_x(),
                             event.y_root - self.root.winfo_y())
        self._dragged = False

    def _drag_move(self, event):
        if self._resizing:          # 正在拖右下角缩放，别同时挪窗口
            return
        off = getattr(self, "_drag_offset", None)
        if not off:
            return
        x = event.x_root - off[0]
        y = event.y_root - off[1]
        self._dragged = True
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, _event):
        if getattr(self, "_dragged", False):
            self._save_position()

    def _save_position(self):
        self.cfg.data["window"] = {"x": self.root.winfo_x(), "y": self.root.winfo_y()}
        self.cfg.save()

    # ---- 数据刷新 ----
    def refresh_now(self, manual=False):
        if not self.alive:
            return
        if manual and self.timer_id:
            try:
                self.root.after_cancel(self.timer_id)
            except Exception:
                pass
            self.timer_id = None

        key = self.cfg.get_api_key()
        if not key and not self.demo:
            self.error = None
            self._render()
            # 只有用户主动点"立即刷新"时才弹设置窗；启动时不弹，避免抢焦点
            if manual:
                self.open_settings()
            return

        if self.fetching:
            return
        self.fetching = True
        self.error = None
        self._render()
        threading.Thread(target=self._worker, args=(key,), daemon=True).start()

    def _worker(self, key):
        try:
            data = _demo_fetch() if self.demo else fetch_balance(key)
            self.queue.put(("ok", data))
        except BalanceError as exc:
            self.queue.put(("err", str(exc)))
        except Exception as exc:  # 兜底，避免线程静默死掉
            log("抓取异常：\n" + traceback.format_exc())
            self.queue.put(("err", f"未知错误：{exc}"))

    def _poll_queue(self):
        if not self.alive:
            return
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "ok":
                    self._on_success(payload)
                else:
                    self._on_error(payload)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _on_success(self, data):
        self.fetching = False
        self.error = None
        prev = self._prev_total
        self.last = data
        self._prev_total = data.get("total")
        self.cfg.data["last"] = data
        self.cfg.save()
        self._render()
        if prev is not None and abs(float(prev) - float(data.get("total", 0.0))) > 0.005:
            self._flash()
        self.retry_delay = FIRST_RETRY
        self._schedule(self.cfg.refresh_seconds)

    def _on_error(self, message):
        self.fetching = False
        self.error = message
        self._render()
        self._schedule(self.retry_delay)
        # 退避上限跟着刷新频率走，但别低于 60 秒——网络断了不该每 20 秒猛敲一次
        self.retry_delay = min(self.retry_delay * 2, max(60, self.cfg.refresh_seconds))

    def _flash(self):
        # 余额变化时闪一下：用当前档位色提亮，别用品牌蓝把色彩语义打断
        base = self._band_colors[self._band] if self._band >= 0 else ACCENT
        self.canvas.itemconfigure(self.amount_id, fill=_lighten(base, 0.45))
        if self.flash_job:
            try:
                self.root.after_cancel(self.flash_job)
            except Exception:
                pass
        self.flash_job = self.root.after(1400, self._end_flash)

    def _end_flash(self):
        self.flash_job = None
        if self.alive:
            self._render()

    def _schedule(self, delay: float):
        if not self.alive:
            return
        if self.timer_id:
            try:
                self.root.after_cancel(self.timer_id)
            except Exception:
                pass
        self.timer_id = self.root.after(int(delay * 1000), self._on_timer)

    def _on_timer(self):
        self.timer_id = None
        self.refresh_now()

    # ---- 设置窗口 ----
    def open_settings(self):
        if self.dialog is not None:
            try:
                self.dialog.top.lift()
                self.dialog.entry.focus_set()
                return
            except Exception:
                self.dialog = None
        self.dialog = SettingsDialog(self)

    def open_bands(self):
        if self.bands_dialog is not None:
            try:
                self.bands_dialog.top.lift()
                return
            except Exception:
                self.bands_dialog = None
        self.bands_dialog = BandsDialog(self)

    def open_estimate(self):
        if self.estimate_dialog is not None:
            try:
                self.estimate_dialog.top.lift()
                return
            except Exception:
                self.estimate_dialog = None
        self.estimate_dialog = EstimateDialog(self)

    def quit_app(self):
        self.alive = False
        # 留个标记：用户是主动退出的，跟随进程不要马上把它拉回来
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            (CONFIG_DIR / "paused.flag").write_text("paused", encoding="utf-8")
        except Exception:
            pass
        for name in ("timer_id", "_dsh_job", "_notice_job", "_resize_job"):
            handle = getattr(self, name, None)
            if handle:
                try:
                    self.root.after_cancel(handle)
                except Exception:
                    pass
                setattr(self, name, None)
        self._hide_toast()
        try:
            self._save_position()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


# ---------------- 首次运行的安全提示 ----------------
SAFETY_HEADLINE = "请勿在公用电脑上使用"
SAFETY_BODY = (
    "挂件会把 DeepSeek API Key 加密后保存在本机，但 Windows 的加密只绑定「当前用户」——"
    "同一台电脑、同一个账户下的其它程序，仍然可以把它解出来。\n\n"
    "公用电脑、网吧、实验室共用机、他人可能登录的电脑，都存在 Key 泄露、"
    "余额被别人花掉的风险。"
)
SAFETY_FOOT = "确认这是你自己的、只有你能登录的电脑，再继续。"


def ask_safety_warning(root) -> bool:
    """首次运行弹一次安全提示；必须点「我已了解」才继续。

    这是程序里唯一一处主动抢焦点的地方——故意为之：用户必须先看见这段话。
    """
    dpi_scale = max(1.0, min(system_dpi() / 96.0, 3.0))

    def px(value):
        return int(round(value * dpi_scale))

    family = pick_family(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"], "Segoe UI")
    f_head = tkfont.Font(family=family, size=-px(14), weight="bold")
    f_body = tkfont.Font(family=family, size=-px(10))
    f_ui = tkfont.Font(family=family, size=-px(10))
    f_ui_bold = tkfont.Font(family=family, size=-px(10), weight="bold")

    top = tk.Toplevel(root)
    top.title("DeepSeekBalanceWarning")     # 独特标题：截图/调试能精确定位
    # 不用系统标题栏：根窗口还藏着时 DWM 深色标题栏不生效，会露出白条。
    # 自绘边框 + 可拖动，和卡片风格一致。
    top.overrideredirect(True)
    top.configure(bg=BORDER)
    top.attributes("-topmost", True)
    answer = {"ok": False}

    frame = tk.Frame(top, bg=BG, padx=px(22), pady=px(18))
    frame.pack(fill="both", expand=True, padx=1, pady=1)

    def start_drag(event):
        top._drag = (event.x_root - top.winfo_x(), event.y_root - top.winfo_y())

    def on_drag(event):
        offset = getattr(top, "_drag", None)
        if offset:
            top.geometry(f"+{event.x_root - offset[0]}+{event.y_root - offset[1]}")

    headline = tk.Label(frame, text=SAFETY_HEADLINE, bg=BG, fg=ERR, font=f_head, anchor="w")
    headline.pack(fill="x")
    for widget in (frame, headline):
        widget.bind("<Button-1>", start_drag)
        widget.bind("<B1-Motion>", on_drag)

    tk.Label(frame, text=SAFETY_BODY, bg=BG, fg=FG, font=f_body, justify="left",
             anchor="w", wraplength=px(380)).pack(fill="x", pady=(px(10), px(8)))
    tk.Label(frame, text=SAFETY_FOOT, bg=BG, fg=WARN, font=f_ui_bold, justify="left",
             anchor="w", wraplength=px(380)).pack(fill="x")

    row = tk.Frame(frame, bg=BG)
    row.pack(fill="x", pady=(px(16), 0))

    def accept():
        answer["ok"] = True
        top.destroy()

    def decline():
        answer["ok"] = False
        top.destroy()

    tk.Button(row, text="我已了解，继续使用", command=accept, relief="flat", bd=0,
              bg=ACCENT, fg="#ffffff", activebackground="#3d5ae0", activeforeground="#ffffff",
              overrelief="flat", cursor="hand2", font=f_ui,
              padx=px(14), pady=px(5), highlightthickness=0).pack(side="left")
    tk.Button(row, text="退出", command=decline, relief="flat", bd=0,
              bg=BG_SOFT, fg=FG, activebackground="#2e3644", activeforeground=FG,
              overrelief="flat", cursor="hand2", font=f_ui,
              padx=px(14), pady=px(5), highlightthickness=0).pack(side="left", padx=(px(8), 0))

    top.protocol("WM_DELETE_WINDOW", decline)
    top.update_idletasks()
    width, height = top.winfo_reqwidth(), top.winfo_reqheight()
    vx, vy, vw, vh = virtual_screen()
    x = max(vx, vx + (vw - width) // 2)
    y = max(vy, vy + (vh - height) // 2 - px(40))
    top.geometry(f"+{x}+{y}")
    try:
        top.grab_set()
        top.focus_force()
    except Exception:
        pass
    root.wait_window(top)
    return answer["ok"]


# ---------------- 设置对话框 ----------------
class SettingsDialog:
    def __init__(self, app: BalanceWidget):
        self.app = app
        self._result = None
        self._pending = False

        self.top = tk.Toplevel(app.root)
        self.top.title("DeepSeek 余额 · 设置")
        self.top.configure(bg=BG)
        self.top.resizable(False, False)
        self.top.attributes("-topmost", True)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._place()
        try:
            self.top.grab_set()
        except Exception:
            pass
        self.entry.focus_set()
        self.top.after(120, self._poll)

    def _build(self):
        f = tk.Frame(self.top, bg=BG, padx=self.app.px_dpi(18), pady=self.app.px_dpi(16))
        f.pack(fill="both", expand=True)

        tk.Label(f, text="DeepSeek API Key", bg=BG, fg=FG, font=self.app.f_ui_bold,
                 anchor="w").pack(fill="x")

        # 每次填 Key 都提醒一遍：这台机器是不是自己的
        tk.Label(f, text="⚠ 公用电脑请勿使用——同账户下的其它程序能解开本机保存的 Key。",
                 bg=BG, fg=ERR, font=self.app.f_ui_small, anchor="w",
                 justify="left", wraplength=self.app.px_dpi(330)).pack(
            fill="x", pady=(self.app.px_dpi(4), 0))

        self.entry = tk.Entry(
            f, width=46, bg=BG_SOFT, fg=FG, insertbackground=ACCENT, relief="flat",
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
            font=self.app.f_ui, show="*", exportselection=False)
        self.entry.pack(fill="x", pady=(self.app.px_dpi(8), self.app.px_dpi(6)),
                        ipady=self.app.px_dpi(5))
        self.entry.bind("<Return>", lambda e: self._submit())
        self.entry.bind("<Escape>", lambda e: self.close())

        # 输入框里是密码：屏蔽一切把明文送进剪贴板的路径（复制/剪切）
        for sequence in ("<Control-c>", "<Control-C>", "<Control-x>", "<Control-X>",
                         "<Control-Insert>", "<<Copy>>", "<<Cut>>"):
            self.entry.bind(sequence, self._block_copy)

        existing = self.app.cfg.get_api_key()
        if existing:
            self.entry.insert(0, existing)
            hint_text = ("已保存的 Key 以 * 显示；要更换就全选后粘贴新的。\n"
                         "只保存在本机（Windows DPAPI 加密），输入框禁止复制。")
        else:
            hint_text = ("在 platform.deepseek.com 的「API keys」页面创建。\n"
                         "只保存在本机（Windows DPAPI 加密），输入框禁止复制。")

        tk.Label(
            f, text=hint_text, bg=BG, fg=MUTED, font=self.app.f_ui_small,
            justify="left", anchor="w", wraplength=self.app.px_dpi(330)).pack(fill="x")

        self.status = tk.Label(f, text="", bg=BG, fg=MUTED, font=self.app.f_ui_small,
                               anchor="w")
        self.status.pack(fill="x", pady=(self.app.px_dpi(8), self.app.px_dpi(6)))

        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.btn_save = tk.Button(
            row, text="验证并保存", command=self._submit, relief="flat", bd=0,
            bg=ACCENT, fg="#ffffff", activebackground="#3d5ae0", activeforeground="#ffffff",
            overrelief="flat", cursor="hand2", font=self.app.f_ui,
            padx=self.app.px_dpi(14), pady=self.app.px_dpi(4), highlightthickness=0)
        self.btn_save.pack(side="left")
        self.btn_cancel = tk.Button(
            row, text="取消", command=self.close, relief="flat", bd=0,
            bg=BG_SOFT, fg=FG, activebackground="#2e3644", activeforeground=FG,
            overrelief="flat", cursor="hand2", font=self.app.f_ui,
            padx=self.app.px_dpi(14), pady=self.app.px_dpi(4), highlightthickness=0)
        self.btn_cancel.pack(side="left", padx=(self.app.px_dpi(8), 0))

        if self.app.cfg.has_stored_key():
            self.btn_clear = tk.Button(
                row, text="清除", command=self._clear, relief="flat", bd=0,
                bg=BG, fg=MUTED, activebackground=BG, activeforeground=ERR,
                cursor="hand2", font=self.app.f_ui_small, highlightthickness=0)
            self.btn_clear.pack(side="right")

    def _place(self):
        self.top.update_idletasks()
        w, h = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        x = self.app.root.winfo_x() + self.app.W - w
        y = self.app.root.winfo_y() + self.app.H + self.app.px_dpi(10)
        vx, vy, vw, vh = virtual_screen()
        x = max(vx, min(x, vx + vw - w))
        y = max(vy, min(y, vy + vh - h))
        self.top.geometry(f"+{x}+{y}")
        set_dark_title_bar(self.top)
        self.top.after(150, lambda: set_dark_title_bar(self.top))

    @staticmethod
    def _block_copy(_event=None):
        """拦住复制/剪切：输入框里的 Key 不允许被导出到剪贴板。"""
        return "break"

    def _set_status(self, text: str, color: str = MUTED):
        self.status.configure(text=text, fg=color)

    def _submit(self):
        if self._pending:
            return
        key = self.entry.get().strip()
        if not key:
            self._set_status("请先粘贴 API Key", ERR)
            return
        if len(key) < 12 or " " in key:
            self._set_status("这个 Key 看起来不太对，请检查是否复制完整", ERR)
            return

        self._pending = True
        self._result = None
        self.btn_save.configure(state="disabled")
        self.btn_cancel.configure(state="disabled")
        self._set_status("正在验证…", MUTED)
        threading.Thread(target=self._validate, args=(key,), daemon=True).start()

    def _validate(self, key):
        try:
            fetch_balance(key)
            self._result = (True, key)
        except BalanceError as exc:
            self._result = (False, str(exc))
        except Exception as exc:
            log("验证 Key 异常：\n" + traceback.format_exc())
            self._result = (False, f"验证失败：{exc}")

    def _poll(self):
        try:
            if not self.top.winfo_exists():
                return
            if self._result is None:
                self.top.after(120, self._poll)
                return
            ok, payload = self._result
            self._pending = False
            self.btn_save.configure(state="normal")
            self.btn_cancel.configure(state="normal")
            if ok:
                self.app.cfg.set_api_key(payload)
                self._set_status("已保存，正在刷新余额…", OK)
                self.close()
                self.app.refresh_now(manual=True)
            else:
                self._set_status(str(payload), ERR)
                self._result = None
                self.top.after(120, self._poll)
        except tk.TclError:
            return

    def _clear(self):
        if not messagebox.askyesno("清除 API Key", "确定要删除本机保存的 API Key 吗？",
                                   parent=self.top):
            return
        self.app.cfg.set_api_key("")
        self.entry.delete(0, "end")
        self._set_status("已清除保存的 Key", WARN)
        self.btn_clear.destroy()

    def close(self):
        try:
            self.top.grab_release()
        except Exception:
            pass
        self.app.dialog = None
        try:
            self.top.destroy()
        except Exception:
            pass


# ---------------- 警戒线设置 ----------------
class BandsDialog:
    """自定义五档警戒线：4 个阈值输入框，必须严格递减。"""

    def __init__(self, app):
        self.app = app
        px = app.px_dpi
        self.top = tk.Toplevel(app.root)
        self.top.title("警戒线设置")
        self.top.configure(bg=BG)
        self.top.resizable(False, False)
        self.top.attributes("-topmost", True)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.entries = []
        self._build(px)
        self._place()
        try:
            self.top.grab_set()
        except Exception:
            pass
        set_dark_title_bar(self.top)
        self.top.after(150, lambda: set_dark_title_bar(self.top))
        self.entries[0].focus_set()

    def _swatch(self, parent, color, px):
        canvas = tk.Canvas(parent, width=px(12), height=px(12), bg=BG,
                           highlightthickness=0, bd=0)
        canvas.pack(side="left", padx=(0, px(8)))
        canvas.create_rectangle(0, 0, px(12), px(12), fill=color, outline="")
        return canvas

    def _build(self, px):
        self.model = self.app.cfg.model
        self.link = self.app.cfg.band_link
        self.model_buttons = {}

        frame = tk.Frame(self.top, bg=BG, padx=px(18), pady=px(16))
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="余额警戒线（元）", bg=BG, fg=FG, font=self.app.f_ui_bold,
                 anchor="w").pack(fill="x")

        segment = tk.Frame(frame, bg=BG)
        segment.pack(fill="x", pady=(px(8), px(4)))
        for label, key in (("Flash", "flash"), ("Pro", "pro")):
            button = tk.Button(
                segment, text=label, command=lambda k=key: self._select_model(k),
                relief="flat", bd=0, cursor="hand2", font=self.app.f_ui,
                padx=px(14), pady=px(3), highlightthickness=0,
                activebackground=ACCENT, activeforeground="#ffffff")
            button.pack(side="left", padx=(0, px(6)))
            self.model_buttons[key] = button

        self.hint = tk.Label(frame, text="", bg=BG, fg=MUTED, font=self.app.f_ui_small,
                             anchor="w", justify="left", wraplength=px(300))
        self.hint.pack(fill="x", pady=(0, px(8)))

        colors = self.app._band_colors
        names = ("青", "酸橙", "柠檬黄", "橙")
        for index in range(4):
            row = tk.Frame(frame, bg=BG)
            row.pack(fill="x", pady=px(2))
            self._swatch(row, colors[index], px)
            name_box = tk.Frame(row, bg=BG, width=px(46), height=px(16))
            name_box.pack(side="left")
            name_box.pack_propagate(False)
            tk.Label(name_box, text=names[index], anchor="w", bg=BG, fg=MUTED,
                     font=self.app.f_ui_small).pack(fill="both")
            tk.Label(row, text="≥", bg=BG, fg=MUTED,
                     font=self.app.f_ui).pack(side="left", padx=(px(6), px(6)))
            entry = tk.Entry(row, width=9, bg=BG_SOFT, fg=FG, insertbackground=ACCENT,
                             relief="flat", highlightthickness=1,
                             highlightbackground=BORDER, highlightcolor=ACCENT,
                             font=self.app.f_ui, justify="right")
            entry.pack(side="left", ipady=px(3))
            entry.bind("<Return>", lambda _e: self._save())
            entry.bind("<Escape>", lambda _e: self.close())
            self.entries.append(entry)

        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x", pady=(px(2), 0))
        self._swatch(row, colors[4], px)
        name_box = tk.Frame(row, bg=BG, width=px(46), height=px(16))
        name_box.pack(side="left")
        name_box.pack_propagate(False)
        tk.Label(name_box, text="大红", anchor="w", bg=BG, fg=MUTED,
                 font=self.app.f_ui_small).pack(fill="both")
        tk.Label(row, text="<", bg=BG, fg=MUTED,
                 font=self.app.f_ui).pack(side="left", padx=(px(6), px(6)))
        self.last_value = tk.Label(row, text="", bg=BG, fg=ERR, font=self.app.f_ui_bold)
        self.last_value.pack(side="left")

        self.entries[3].bind("<KeyRelease>", lambda _e: self._update_last())

        self.link_btn = tk.Button(
            frame, text="", command=self._toggle_link, relief="flat", bd=0,
            bg=BG, fg=MUTED, activebackground=BG, activeforeground=FG,
            cursor="hand2", font=self.app.f_ui_small, highlightthickness=0,
            anchor="w", padx=0, pady=px(3))
        self.link_btn.pack(fill="x", pady=(px(8), 0))
        self._refresh_link()

        self.status = tk.Label(frame, text="", bg=BG, fg=MUTED, font=self.app.f_ui_small,
                               anchor="w")
        self.status.pack(fill="x", pady=(px(6), px(4)))

        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x")
        self.btn_reset = tk.Button(
            row, text="恢复默认", command=self._reset, relief="flat", bd=0,
            bg=BG, fg=MUTED, activebackground=BG, activeforeground=FG,
            cursor="hand2", font=self.app.f_ui_small, highlightthickness=0)
        self.btn_reset.pack(side="left")
        self.btn_save = tk.Button(
            row, text="保存", command=self._save, relief="flat", bd=0,
            bg=ACCENT, fg="#ffffff", activebackground="#3d5ae0", activeforeground="#ffffff",
            overrelief="flat", cursor="hand2", font=self.app.f_ui,
            padx=px(16), pady=px(4), highlightthickness=0)
        self.btn_save.pack(side="right")
        self.btn_cancel = tk.Button(
            row, text="取消", command=self.close, relief="flat", bd=0,
            bg=BG_SOFT, fg=FG, activebackground="#2e3644", activeforeground=FG,
            overrelief="flat", cursor="hand2", font=self.app.f_ui,
            padx=px(14), pady=px(4), highlightthickness=0)
        self.btn_cancel.pack(side="right", padx=(0, px(8)))

        self._select_model(self.model)

    def _select_model(self, model):
        self.model = model
        for key, button in self.model_buttons.items():
            selected = key == model
            button.configure(bg=ACCENT if selected else BG_SOFT,
                             fg="#ffffff" if selected else MUTED)
        for entry, value in zip(self.entries, self.app.cfg.edges_for(model)):
            entry.delete(0, "end")
            entry.insert(0, f"{value:.2f}")
        self._update_last()
        self.hint.configure(
            text=("正在编辑 Flash 的警戒线；联动开启时 Pro 会按 ×4 同步。"
                  if model == "flash" else
                  "正在编辑 Pro 的警戒线；联动开启时 Flash 会按 ÷4 同步。"))
        self.status.configure(text="")

    def _toggle_link(self):
        self.link = not self.link
        self._refresh_link()

    def _refresh_link(self):
        self.link_btn.configure(
            text=("✓ " if self.link else "   ") + "Flash ↔ Pro 联动（×4）",
            fg=FG if self.link else MUTED)

    def _update_last(self):
        try:
            value = float(self.entries[3].get().strip())
            self.last_value.configure(text=f"{value:,.2f}")
        except ValueError:
            self.last_value.configure(text="?")

    def _error(self, message):
        self.status.configure(text=message, fg=ERR)

    def _save(self):
        try:
            values = [float(entry.get().strip()) for entry in self.entries]
        except ValueError:
            self._error("四个数值都要填数字")
            return
        if any(value < 0 for value in values):
            self._error("不能是负数")
            return
        if not all(values[i] > values[i + 1] for i in range(3)):
            self._error("必须从高到低严格递减")
            return

        cfg = self.app.cfg
        cfg.model = self.model
        cfg.band_link = self.link
        if not cfg.set_edges(self.model, values):
            self._error("数值不合法，没有保存")
            return
        self.app.reload_bands()
        self.app._render()
        self.close()

    def _reset(self):
        for entry, value in zip(self.entries, self.app.cfg.default_edges(self.model)):
            entry.delete(0, "end")
            entry.insert(0, f"{value:.2f}")
        self._update_last()
        self.status.configure(text="已填入默认值，点保存生效", fg=MUTED)

    def _place(self):
        self.top.update_idletasks()
        width, height = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        x = self.app.root.winfo_x() + self.app.W - width
        y = self.app.root.winfo_y() + self.app.H + self.app.px_dpi(10)
        vx, vy, vw, vh = virtual_screen()
        x = max(vx, min(x, vx + vw - width))
        y = max(vy, min(y, vy + vh - height))
        self.top.geometry(f"+{x}+{y}")

    def close(self):
        try:
            self.top.grab_release()
        except Exception:
            pass
        self.app.bands_dialog = None
        try:
            self.top.destroy()
        except Exception:
            pass


# ---------------- 预估档位设置 ----------------
class EstimateDialog:
    """自定义四个预估档位：名称 + 单价（元/次）。

    "预计使用次数"就是余额 ÷ 单价，所以改单价等于改次数；右边实时给出现在还能做几次。
    """

    def __init__(self, app):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title("预估档位设置")
        self.top.configure(bg=BG)
        self.top.resizable(False, False)
        self.top.attributes("-topmost", True)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.names, self.prices, self.previews = [], [], []
        self._build(app.px_dpi)
        self._place()
        try:
            self.top.grab_set()
        except Exception:
            pass
        set_dark_title_bar(self.top)
        self.top.after(150, lambda: set_dark_title_bar(self.top))
        self.names[0].focus_set()

    def _build(self, px):
        frame = tk.Frame(self.top, bg=BG, padx=px(18), pady=px(16))
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="预估档位", bg=BG, fg=FG, font=self.app.f_ui_bold,
                 anchor="w").pack(fill="x")
        tk.Label(frame,
                 text="填「一次大概花多少钱」，余额 ÷ 单价 = 还能做几次。\n"
                      "Pro 再 ×4、高峰再 ×2，都在这个单价上自动折算。",
                 bg=BG, fg=MUTED, font=self.app.f_ui_small, justify="left", anchor="w",
                 wraplength=px(340)).pack(fill="x", pady=(px(6), px(10)))

        head = tk.Frame(frame, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="名称（最多 12 字）", bg=BG, fg=DIM, font=self.app.f_ui_small,
                 width=18, anchor="w").pack(side="left")
        tk.Label(head, text="单价 元/次", bg=BG, fg=DIM, font=self.app.f_ui_small,
                 width=9, anchor="w").pack(side="left", padx=(px(6), 0))
        tk.Label(head, text="现在还能做", bg=BG, fg=DIM, font=self.app.f_ui_small,
                 anchor="w").pack(side="left", padx=(px(10), 0))

        for item in self.app.cfg.estimate_items():
            row = tk.Frame(frame, bg=BG)
            row.pack(fill="x", pady=px(3))
            name = tk.Entry(row, width=18, bg=BG_SOFT, fg=FG, insertbackground=ACCENT,
                            relief="flat", highlightthickness=1,
                            highlightbackground=BORDER, highlightcolor=ACCENT,
                            font=self.app.f_ui)
            name.insert(0, item["name"])
            name.pack(side="left", ipady=px(3))
            price = tk.Entry(row, width=9, bg=BG_SOFT, fg=FG, insertbackground=ACCENT,
                             relief="flat", highlightthickness=1,
                             highlightbackground=BORDER, highlightcolor=ACCENT,
                             font=self.app.f_ui, justify="right")
            price.insert(0, f"{item['price']:g}")
            price.pack(side="left", padx=(px(6), px(10)), ipady=px(3))
            preview = tk.Label(row, text="", bg=BG, fg=MUTED, font=self.app.f_ui_small,
                               anchor="w")
            preview.pack(side="left")
            self.names.append(name)
            self.prices.append(price)
            self.previews.append(preview)
            for widget in (name, price):
                widget.bind("<KeyRelease>", lambda _e: self._refresh())
                widget.bind("<Return>", lambda _e: self._save())
                widget.bind("<Escape>", lambda _e: self.close())

        self.status = tk.Label(frame, text="", bg=BG, fg=MUTED,
                               font=self.app.f_ui_small, anchor="w")
        self.status.pack(fill="x", pady=(px(8), px(4)))

        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x")
        tk.Button(row, text="恢复默认", command=self._reset, relief="flat", bd=0,
                  bg=BG, fg=MUTED, activebackground=BG, activeforeground=FG,
                  cursor="hand2", font=self.app.f_ui_small,
                  highlightthickness=0).pack(side="left")
        tk.Button(row, text="保存", command=self._save, relief="flat", bd=0,
                  bg=ACCENT, fg="#ffffff", activebackground="#3d5ae0",
                  activeforeground="#ffffff", overrelief="flat", cursor="hand2",
                  font=self.app.f_ui, padx=px(16), pady=px(4),
                  highlightthickness=0).pack(side="right")
        tk.Button(row, text="取消", command=self.close, relief="flat", bd=0,
                  bg=BG_SOFT, fg=FG, activebackground="#2e3644", activeforeground=FG,
                  overrelief="flat", cursor="hand2", font=self.app.f_ui,
                  padx=px(14), pady=px(4),
                  highlightthickness=0).pack(side="right", padx=(0, px(8)))

        self._refresh()

    def _refresh(self):
        """实时算"现在还能做几次"，让用户改单价时看得到效果。"""
        total = float(self.app.last.get("total", 0.0)) if self.app.last else 0.0
        mult = 1.0
        if self.app.cfg.model == "pro":
            mult *= MODEL_PRO_FACTOR
        if is_peak_time():
            mult *= 2.0
        for price_entry, label in zip(self.prices, self.previews):
            try:
                price = float(price_entry.get().strip())
            except ValueError:
                label.configure(text="?", fg=ERR)
                continue
            if price <= 0:
                label.configure(text="?", fg=ERR)
                continue
            label.configure(text=f"{format_count(total / (price * mult))} 次", fg=MUTED)

    def _reset(self):
        for entry, (name, price) in zip(self.names, ESTIMATE_PRICES):
            entry.delete(0, "end")
            entry.insert(0, name)
        for entry, (_name, price) in zip(self.prices, ESTIMATE_PRICES):
            entry.delete(0, "end")
            entry.insert(0, f"{price:g}")
        self.status.configure(text="已填入默认值，点保存生效", fg=MUTED)
        self._refresh()

    def _save(self):
        pairs = []
        for name_entry, price_entry in zip(self.names, self.prices):
            name = name_entry.get().strip()
            if not name:
                self.status.configure(text="名称不能为空", fg=ERR)
                return
            if len(name) > 12:
                self.status.configure(text="名称最多 12 个字", fg=ERR)
                return
            try:
                price = float(price_entry.get().strip())
            except ValueError:
                self.status.configure(text="单价要填数字", fg=ERR)
                return
            if price <= 0:
                self.status.configure(text="单价必须大于 0", fg=ERR)
                return
            pairs.append((name, price))
        if not self.app.cfg.set_estimate_items(pairs):
            self.status.configure(text="保存失败（数值不合法）", fg=ERR)
            return
        self.close()

    def _place(self):
        self.top.update_idletasks()
        width, height = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        x = self.app.root.winfo_x() + self.app.W - width
        y = self.app.root.winfo_y() + self.app.H + self.app.px_dpi(10)
        vx, vy, vw, vh = virtual_screen()
        x = max(vx, min(x, vx + vw - width))
        y = max(vy, min(y, vy + vh - height))
        self.top.geometry(f"+{x}+{y}")

    def close(self):
        try:
            self.top.grab_release()
        except Exception:
            pass
        self.app.estimate_dialog = None
        try:
            self.top.destroy()
        except Exception:
            pass


# ---------------- 入口 ----------------
def run_selftest() -> int:
    """自检：用 Tk 的 event_generate 直接给控件发事件，不碰真实鼠标键盘。

    这样验证界面时既不会抢走用户的指针，也不会被别的自动化脚本干扰。
    """
    # 打包后控制台是 GBK，打印 ✔/⚠ 这类字符会抛 UnicodeEncodeError，先改成 UTF-8。
    # windowed 模式下 sys.stdout 是 None，reconfigure 会失败——忽略即可。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    enable_dpi_awareness()
    cfg = Config()
    cfg.save = lambda: None            # 自检不写盘，别污染用户配置
    root = tk.Tk()
    root.withdraw()
    app = BalanceWidget(root, cfg, demo=True, forced_pos=(240, 180))
    root.deiconify()
    root.update()

    # 自检不写真实的历史文件，也不去读真实 DSH 状态（下面用假数据单独测）
    app.selftest = True
    app.cfg.data["dsh_status"] = False
    app.cfg.data["follow_model"] = False
    # 用默认警戒线跑分档断言，不受用户自定义值影响
    app.cfg.model = "flash"
    app.cfg.data["bands"] = {
        "flash": list(BAND_EDGES_DEFAULT),
        "pro": [round(v * MODEL_PRO_FACTOR, 2) for v in BAND_EDGES_DEFAULT],
    }
    app.reload_bands()

    fails = []

    def check(name, ok, detail=""):
        print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""),
              flush=True)
        if not ok:
            fails.append(name)

    def press_drag(x0, y0, dx, dy):
        app.canvas.event_generate("<Button-1>", x=40, y=30,
                                  rootx=x0 + 40, rooty=y0 + 30)
        app.canvas.event_generate("<B1-Motion>", x=40 + dx, y=30 + dy,
                                  rootx=x0 + 40 + dx, rooty=y0 + 30 + dy)
        app.canvas.event_generate("<ButtonRelease-1>", x=40 + dx, y=30 + dy,
                                  rootx=x0 + 40 + dx, rooty=y0 + 30 + dy)
        root.update()

    check("初始尺寸自洽", (root.winfo_width(), root.winfo_height()) == (app.W, app.H),
          f"{root.winfo_width()}x{root.winfo_height()} vs {app.W}x{app.H}")
    check("缩放换算", app.W == int(round(BASE_W * app.scale)), f"scale={app.scale:.2f}")

    x0, y0 = root.winfo_x(), root.winfo_y()
    press_drag(x0, y0, 100, 60)
    check("拖动生效", (root.winfo_x(), root.winfo_y()) == (x0 + 100, y0 + 60),
          f"{root.winfo_x()},{root.winfo_y()} 期望 {x0 + 100},{y0 + 60}")

    app.locked = True
    x1, y1 = root.winfo_x(), root.winfo_y()
    press_drag(x1, y1, 200, 150)
    check("锁定位置生效", (root.winfo_x(), root.winfo_y()) == (x1, y1),
          f"{root.winfo_x()},{root.winfo_y()} 期望 {x1},{y1}")
    app.locked = False

    check("菜单结构", app.menu.index("end") == 16, f"end={app.menu.index('end')}")

    # 右侧"增值"竖条：贴右边缘、竖排、不压金额、不撞右上角曲线和右下角 chip
    pts = app.canvas.coords(app.topup_bg)
    bx0, bx1 = min(pts[::2]), max(pts[::2])
    by0, by1 = min(pts[1::2]), max(pts[1::2])
    check("增值贴右边缘", bx1 >= app.W - app.px(TOPUP_INSET) - 2 and bx1 <= app.W,
          f"x1={bx1} W={app.W}")
    check("增值字样竖排", app.canvas.itemcget(app.topup_text, "text") == "增\n值",
          repr(app.canvas.itemcget(app.topup_text, "text")))
    check("增值不压金额", bx0 > app.px(PAD_L) + app.text_width(),
          f"按钮左 {bx0} vs 文字右 {app.px(PAD_L) + app.text_width()}")
    chip_top = app.px(Y_FOOTER) - app.px(1)
    spark_bottom = app.px(Y_TITLE + FONT_TITLE)
    check("增值夹在标题和 chip 之间",
          by0 > spark_bottom and by1 < chip_top,
          f"y {by0}..{by1}，上界 {spark_bottom}，下界 {chip_top}")

    # 点击行为：把浏览器换成假的，只看有没有拼对地址，不真的弹窗
    opened = []
    real_open = webbrowser.open
    webbrowser.open = lambda url, *a, **k: opened.append(url) or True
    try:
        app._on_topup()
    finally:
        webbrowser.open = real_open
    check("增值点开充值页", opened == [TOPUP_URL], str(opened))
    check("增值可点击", bool(app.canvas.tag_bind("topup", "<Button-1>")))
    check("未误判注入输入", input_is_injected() is False)

    # 单实例锁：同一个名字第二次必须拿不到（用独立名字，别和真挂件抢）
    first = _create_mutex("Local\\DeepSeekBalanceSelftest")
    second = _create_mutex("Local\\DeepSeekBalanceSelftest")
    check("单实例互斥", first is not None and second is None,
          f"{bool(first)}/{bool(second)}")
    if first:
        _kernel32().CloseHandle(first)

    # 五档警戒色：边界值归入较高一档
    cases = [(520.0, 0), (200.0, 0), (168.0, 1), (86.0, 2), (34.0, 3),
             (20.0, 3), (12.4, 4)]
    got = [app.band_index(value) for value, _ in cases]
    check("警戒线分档", got == [expect for _, expect in cases], str(got))

    saved_bands = app.cfg.data.get("bands")
    app.cfg.data["bands"] = {"flash": [100, 50, 50, 10], "pro": [400, 200, 200, 40]}
    app.reload_bands()
    fallback_ok = (list(app._edges) == list(BAND_EDGES_DEFAULT)
                   and tuple(app._band_colors) == BAND_COLORS_DEFAULT
                   and app.band_index(34.0) == 3)
    check("非法警戒线回退", fallback_ok, f"edges={app._edges}")

    app.cfg.band_link = True
    app.cfg.set_edges("flash", [1000, 500, 200, 100])
    app.reload_bands()
    check("自定义警戒线生效", app.band_index(34.0) == 4,
          f"band={app.band_index(34.0)}（默认阈值下是 3）")
    check("Pro 联动 ×4",
          app.cfg.edges_for("pro") == [4000.0, 2000.0, 800.0, 400.0],
          str(app.cfg.edges_for("pro")))

    app.cfg.set_edges("pro", [800, 400, 160, 80])
    check("Pro 反向带动 Flash",
          app.cfg.edges_for("flash") == [200.0, 100.0, 40.0, 20.0],
          str(app.cfg.edges_for("flash")))

    app.cfg.data["bands"] = saved_bands
    app.cfg.reset_edges("flash")
    app.cfg.reset_edges("pro")
    app.reload_bands()

    # 时间判定 + 预估
    check("高峰判定",
          is_peak_time(datetime.datetime(2026, 10, 19, 10, 0))
          and not is_peak_time(datetime.datetime(2026, 10, 19, 13, 0))
          and not is_peak_time(datetime.datetime(2026, 10, 19, 19, 0))
          and not is_peak_time(datetime.datetime(2026, 10, 24, 10, 0)),
          "")
    check("次数格式", format_count(1709) == "1.7k" and format_count(34.18) == "34", "")

    # 预估口径：相对最初那一版（6 / 1 / 0.2 / 0.02）调保守的倍数，改价时必须一起改这里
    baseline = (6.0, 1.0, 0.2, 0.02)
    ratios = [round(price / base, 2) for (_, price), base in zip(ESTIMATE_PRICES, baseline)]
    check("预估口径调保守", ratios == [1.2, 1.5, 2.0, 2.0], str(ratios))

    # 自定义档位：名称和单价都能改，次数 = 余额 ÷ 单价
    saved_estimate = app.cfg.data.get("estimate")
    app.cfg.model = "flash"
    ok = app.cfg.set_estimate_items([("打包项目", 10), ("改个 bug", 5), ("问一句", 2),
                                     ("打个招呼", 1)])
    counts = app.estimate_counts(100.0)
    check("自定义预估档位",
          ok and [name for name, _ in counts] == ["打包项目", "改个 bug", "问一句", "打个招呼"]
          and abs(counts[0][1] - 10.0) < 1e-6,
          f"{[name for name, _ in counts]} 首项 {counts[0][1]:.1f}")
    check("自定义档位能落盘", app.cfg.estimate_items()[0] == {"name": "打包项目", "price": 10.0},
          str(app.cfg.estimate_items()[:1]))

    app.cfg.data["estimate"] = [{"name": "", "price": 1}] * 4
    check("非法档位回退默认",
          app.estimate_counts(100.0)[0][0] == ESTIMATE_PRICES[0][0],
          app.estimate_counts(100.0)[0][0])
    app.cfg.data["estimate"] = saved_estimate
    if saved_estimate is None:
        app.cfg.data.pop("estimate", None)

    # 刷新频率：四档可选，非法值拒收
    saved_refresh = app.cfg.data.get("refresh_seconds")
    ok = app.cfg.set_refresh(20)
    check("刷新频率可调", ok and app.cfg.refresh_seconds == 20,
          str(app.cfg.refresh_seconds))
    check("非法刷新频率被拒",
          app.cfg.set_refresh(45) is False and app.cfg.refresh_seconds == 20,
          str(app.cfg.refresh_seconds))
    app.cfg.set_refresh(saved_refresh if saved_refresh in REFRESH_ALLOWED else 300)
    app.cfg.data["refresh_seconds"] = saved_refresh
    if saved_refresh is None:
        app.cfg.data.pop("refresh_seconds", None)

    # ---- A/C：DSH 任务状态 ----
    if dsh_state is not None:
        check("模型名归一",
              dsh_state.normalize_model("deepseek-pro") == "pro"
              and dsh_state.normalize_model("deepseek-flash") == "flash"
              and dsh_state.normalize_model("") is None)

        # 版本号不认识时：不崩、仍尽力解析、并标记 compat=False
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fake = {"version": 99,
                    "record": {"identity": {"formatVersion": 99},
                               "rows": {
                                   "turnBoundary": {"val": {"openTurnStartSeq": 5,
                                                            "lastTurn": 2}},
                                   "tokenUsage": {"val": {"totals": {
                                       "outputTokens": 7}}}}}}
            fake_path = Path(tmp) / "s.json"
            fake_path.write_text(json.dumps(fake), encoding="utf-8")
            probed = dsh_state.read_session(str(fake_path))
        check("DSH 版本不认识也能读",
              probed is not None and probed["open_turn"] == 5
              and probed["tokens"]["outputTokens"] == 7,
              str(probed and (probed["open_turn"], probed["tokens"])))
        check("DSH 版本变化被标记",
              probed is not None and probed["compat"] is False
              and (probed["cache_version"], probed["record_version"]) == (99, 99),
              str(probed and (probed["cache_version"], probed["compat"])))

        stamp = time.time()
        running = {"path": "C:/fake/a.json", "mtime": stamp, "age": 0.0, "title": "t",
                   "open_turn": 900, "turn_no": 7, "turns": 7, "steps": 30,
                   "llm_ms": 60000, "tool_ms": 10000,
                   "tokens": {"cacheReadTokens": 1000000, "uncachedInputTokens": 100000,
                              "outputTokens": 200000},
                   "model": "pro", "model_raw": "deepseek-pro", "effort": "max",
                   "pressure": 1000, "window": 1000000, "retry": 0,
                   "last_message": stamp}

        app.cfg.data["follow_model"] = True
        app.cfg.data["dsh_status"] = True
        app.cfg.model = "flash"
        app.reload_bands()
        app._apply_dsh(dict(running))
        check("跟随 DSH 模型", app.cfg.model == "pro", app.cfg.model)
        check("页脚显示运行中", "运行中" in app._status_line()[0], app._status_line()[0])

        finished = dict(running)
        finished["open_turn"] = None
        finished["tokens"] = {"cacheReadTokens": 3000000, "uncachedInputTokens": 300000,
                              "outputTokens": 600000}
        app._apply_dsh(finished)
        notice = (app._notice or ["", "", 0])[0]
        check("结束提醒只报轮次、不报钱",
              "第7轮" in notice and "¥" not in notice, notice)
        check("完成浮窗弹出", app._toast is not None)
        app._hide_toast()

        app.cfg.data["follow_model"] = False
        app.cfg.model = "flash"
        app._apply_dsh(dict(running))
        check("关闭跟进后不改模型", app.cfg.model == "flash", app.cfg.model)
        app._dsh = None
        app._dsh_turn = None
        app._notice = None
        app.cfg.model = "flash"

    app.cfg.model = "flash"
    flash_counts = [value for _, value in app.estimate_counts(1000.0)]
    app.cfg.model = "pro"
    pro_counts = [value for _, value in app.estimate_counts(1000.0)]
    check("Pro 预估 = Flash 的 1/4",
          all(abs(p * 4 - f) < 1e-6 for f, p in zip(flash_counts, pro_counts)),
          f"{flash_counts[0]:.3f} vs {pro_counts[0]:.3f}")
    app.cfg.model = "flash"

    # 余额不足时强制最低档，与阈值无关
    app.fetching = False
    app.error = None
    app.last = {"currency": "CNY", "total": 999.0, "granted": 0.0, "topped": 999.0,
                "available": False, "ts": time.time()}
    app._render()
    check("停用强制最低档",
          app.canvas.itemcget(app.bar_id, "fill").upper() == BAND_COLORS_DEFAULT[4].upper(),
          app.canvas.itemcget(app.bar_id, "fill"))

    # 五段刻度改成油量表：余额越高亮得越多，亮着的段都用当前档位的颜色
    def band_fills():
        return [app.canvas.itemcget(item, "fill").upper() for item in app.band_ids]

    app.cfg.model = "flash"
    app.reload_bands()          # 前面测过 DSH 跟随模型，边界可能还停在 Pro 那套
    app.last = {"currency": "CNY", "total": 520.0, "granted": 0.0, "topped": 520.0,
                "available": True, "ts": time.time()}
    app._render()
    got = band_fills()
    check("满档五段全亮",
          got == [BAND_COLORS_DEFAULT[0].upper()] * 5, str(got))

    app.last = {"currency": "CNY", "total": 12.0, "granted": 0.0, "topped": 12.0,
                "available": True, "ts": time.time()}
    app._render()
    got = band_fills()
    check("低档只剩最右一段",
          got[4] == BAND_COLORS_DEFAULT[4].upper()
          and got[:4] == [BAND_IDLE.upper()] * 4, str(got))

    # 副行：哪一项为 0 就不显示那一项（赠金用完不该还挂个 0.00）
    app.last = {"currency": "CNY", "total": 100.0, "granted": 0.0, "topped": 100.0,
                "available": True, "ts": time.time()}
    app._render()
    sub = app.canvas.itemcget(app.sub_id, "text")
    check("赠金用完不显示", sub == "充值 100.00", sub)

    app.last = {"currency": "CNY", "total": 100.0, "granted": 10.0, "topped": 90.0,
                "available": True, "ts": time.time()}
    app._render()
    sub = app.canvas.itemcget(app.sub_id, "text")
    check("赠金还没用完就照常显示", sub == "赠金 10.00 · 充值 90.00", sub)

    app.last = {"currency": "CNY", "total": 0.0, "granted": 0.0, "topped": 0.0,
                "available": True, "ts": time.time()}
    app._render()
    sub = app.canvas.itemcget(app.sub_id, "text")
    check("两项都为 0 时副行留空", sub == "", repr(sub))
    app.last = None

    app.set_ui_scale(0.90)
    root.update()
    before_w, before_scale = app.W, app.ui_scale
    edge = root.winfo_x() + app.W - 6

    class _E:
        def __init__(self, x):
            self.x_root = x

    # 自检里没有真的按住鼠标，所以把"按键状态/光标位置"这两个探针换掉
    real_pos, real_button = globals().get("_cursor_pos"), globals().get("_left_button_down")
    fake = {"x": edge}
    globals()["_cursor_pos"] = lambda: (fake["x"], 0)
    globals()["_left_button_down"] = lambda: True
    try:
        app._on_grip_press(_E(edge))
        fake["x"] = edge + 40                    # 往右下拖 40px
        app._on_resize_motion(_E(edge + 40))
        app._on_resize_end()
        root.update()
        check("拖右下角能缩放",
              app.W > before_w
              and abs(app.W - int(round(BASE_W * app.dpi_scale * app.ui_scale))) <= 1,
              f"{before_w} -> {app.W}（scale {before_scale:.2f} -> {app.ui_scale:.2f}）")

        # 画布在缩放过程中会被重建，确认鼠标事件仍能冒泡到顶层绑定（否则拖一半就断了）
        app.set_ui_scale(0.90)
        root.update()
        w_event_before = app.W
        edge = root.winfo_x() + app.W - 6
        fake["x"] = edge
        app._on_grip_press(_E(edge))
        root.update()
        fake["x"] = edge + 40
        app.canvas.event_generate("<B1-Motion>", x=5, y=5,
                                  rootx=edge + 40, rooty=root.winfo_y() + app.H - 6)
        root.update()
        w_event_after = app.W
        app._on_resize_end()
        root.update()
        check("重建后事件仍能冒泡", w_event_after > w_event_before,
              f"{w_event_before} -> {w_event_after}")

        # 松开事件丢在窗口外时，靠轮询自愈：按键已经不在了就应该结束缩放
        app.set_ui_scale(0.90)
        root.update()
        fake["x"] = edge
        app._on_grip_press(_E(edge))
        globals()["_left_button_down"] = lambda: False
        app._on_resize_motion(_E(edge + 40))
        root.update()
        check("松开后自动结束缩放",
              app._resizing is False and app.W == int(round(BASE_W * app.dpi_scale * 0.9)),
              f"resizing={app._resizing} W={app.W}")
    finally:
        globals()["_cursor_pos"] = real_pos
        globals()["_left_button_down"] = real_button

    app.set_ui_scale(0.80)
    root.update()
    check("切到 0.80", app.W == int(round(BASE_W * app.dpi_scale * 0.8)), f"W={app.W}")
    app.set_ui_scale(0.90)
    root.update()
    check("切回 0.90", app.W == int(round(BASE_W * app.dpi_scale * 0.9)), f"W={app.W}")

    def finish():
        check("演示数据已刷新", app.last is not None and not app.error,
              f"total={app.last.get('total') if app.last else None} err={app.error}")
        print(f"\n{'全部通过' if not fails else '失败 ' + str(len(fails)) + ' 项: ' + ', '.join(fails)}")
        root.destroy()

    app.refresh_now(manual=True)
    root.after(1500, finish)
    root.mainloop()
    return 0 if not fails else 1


def parse_args(argv):
    demo = "--demo" in argv
    pos = None
    shot = ""
    for arg in argv:
        if arg.startswith("--pos="):
            try:
                xs, ys = arg.split("=", 1)[1].split(",")
                pos = (int(xs), int(ys))
            except Exception:
                pass
        elif arg.startswith("--shot="):
            # 调试用：启动后自动弹出详情/提醒浮窗，方便截图核对版式
            shot = arg.split("=", 1)[1].strip()
    return demo, pos, shot


def main():
    global _ICON_IMAGE
    argv = sys.argv[1:]
    if "--follow" in argv:
        # 看门狗模式：打包后的 exe 用它当"跟随 DSH 启动"的后台进程
        sys.exit(follow_main(debug="--debug" in argv, restart="--restart" in argv,
                             force="--force" in argv))
    if "--selftest" in argv:
        sys.exit(run_selftest())
    demo, pos, shot = parse_args(argv)
    enable_dpi_awareness()

    if not acquire_single_instance():
        # 桌面快捷方式双击、或跟随进程重复拉起时：把已有的挂件提到最前，不弹模态框
        if not raise_existing_widget():
            ctypes.windll.user32.MessageBoxW(
                None, "DeepSeek 余额挂件已经在运行了。", "DeepSeek 余额", 0x40)
        return

    cfg = Config()
    root = tk.Tk()
    root.withdraw()

    icon_path, _ico = ensure_icon()
    try:
        _ICON_IMAGE = tk.PhotoImage(file=str(icon_path))
        root.iconphoto(True, _ICON_IMAGE)
    except Exception:
        log("设置窗口图标失败：\n" + traceback.format_exc())

    try:
        root.tk.call("tk", "scaling", system_dpi() / 72.0)
    except Exception:
        pass

    def on_error(exc_type, exc_value, exc_tb):
        log("界面回调异常：\n" + "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        try:
            messagebox.showerror("DeepSeek 余额", f"程序出错：\n{exc_value}")
        except Exception:
            pass

    root.report_callback_exception = on_error

    # 首次运行：先看安全提示，确认后才知道要不要装 Key。
    # 打包发给别人时，对方第一次打开也会先看到这一段。
    if not cfg.data.get("warning_ack"):
        if not ask_safety_warning(root):
            log("用户没有确认安全提示，退出")
            # 和「退出」一样留标记，否则跟随进程会立刻把它拉回来，警告弹个不停
            try:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                (CONFIG_DIR / "paused.flag").write_text("paused", encoding="utf-8")
            except Exception:
                pass
            try:
                root.destroy()
            except Exception:
                pass
            return
        cfg.data["warning_ack"] = True
        cfg.save()

    app = BalanceWidget(root, cfg, demo=demo, forced_pos=pos)
    root.deiconify()
    root.lift()
    if shot:
        if "details" in shot:
            root.after(1200, app._show_estimate)
        if "notice" in shot:
            root.after(1400, lambda: app._set_notice("✔ 第 19 轮 · ¥0.28", OK, 30))
        if "toast" in shot:
            root.after(1600, lambda: app._show_toast(
                "第 19 轮完成", dsh_state.format_duration(728) if dsh_state else "12 分 08 秒", OK))
        if "menu" in shot:
            def _post():
                app._sync_menu()
                app.menu.post(max(0, app.pos[0] - app.px(210)), app.pos[1])
            root.after(1800, _post)
        if "estimate" in shot:
            root.after(1500, app.open_estimate)
    root.after(800, lambda: log(
        f"启动 pid={os.getpid()} 位置={root.winfo_x()},{root.winfo_y()} "
        f"尺寸={root.winfo_width()}x{root.winfo_height()} 缩放={app.scale}"))
    try:
        root.mainloop()
    finally:
        log("挂件退出")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("启动失败：\n" + traceback.format_exc())
        try:
            ctypes.windll.user32.MessageBoxW(
                None, "启动失败：\n" + traceback.format_exc()[-800:], "DeepSeek 余额", 0x10)
        except Exception:
            pass
        raise
