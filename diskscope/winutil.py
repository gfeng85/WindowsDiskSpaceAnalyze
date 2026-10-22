"""Windows 平台相关工具：提权、格式化、长路径、CJK 对齐。"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 提权

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(extra_args: list[str] | None = None) -> bool:
    """以管理员权限重新启动自己。返回 True 表示已发起提权。"""
    if is_admin():
        return False
    argv = list(sys.argv[1:] if extra_args is None else extra_args)
    # 去掉可能存在的重复标记，避免死循环
    argv = [a for a in argv if a != "--no-elevate"]
    launcher = str(APP_DIR / "run.py")
    params = subprocess.list2cmdline([launcher, *argv])
    exe = sys.executable
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, str(APP_DIR), 1
        )
    except Exception as exc:  # pragma: no cover
        print(f"[!] 提权失败: {exc}")
        return False
    if int(rc) <= 32:
        print("[!] 提权被取消或失败（错误码 %s）" % rc)
        return False
    return True


# ---------------------------------------------------------------- 格式化

def fmt_size(n: float) -> str:
    """人类可读的容量。"""
    n = float(n or 0)
    sign = "-" if n < 0 else ""
    n = abs(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{sign}{int(n)} B"
            return f"{sign}{n:.1f} {unit}"
        n /= 1024.0
    return f"{sign}{n:.1f} TB"


def fmt_delta(n: float) -> str:
    if n == 0:
        return "0"
    return ("+" if n > 0 else "-") + fmt_size(abs(n))


def display_width(s: str) -> int:
    """计算终端显示宽度（CJK 占 2 列）。"""
    w = 0
    for ch in str(s):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s: str, width: int, align: str = "left") -> str:
    s = str(s)
    gap = max(0, width - display_width(s))
    if align == "right":
        return " " * gap + s
    if align == "center":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    return s + " " * gap


def truncate_mid(s: str, width: int) -> str:
    """中间省略，保留头尾，便于识别路径。"""
    if display_width(s) <= width:
        return s
    if width <= 8:
        return s[:width]
    head = (width - 3) // 2
    tail = width - 3 - head
    return s[:head] + "..." + s[-tail:]


# ---------------------------------------------------------------- 路径

DRIVE_LETTER_ABS = lambda p: len(p) >= 2 and p[1] == ":" and p[0].isalpha()


def long_path(path: str) -> str:
    """加 \\\\?\\ 前缀，突破 260 字符限制。"""
    if path.startswith("\\\\?\\"):
        return path
    if DRIVE_LETTER_ABS(path):
        return "\\\\?\\" + path
    return path
