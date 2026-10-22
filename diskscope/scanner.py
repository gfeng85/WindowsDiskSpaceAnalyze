"""目录容量扫描引擎。

设计要点
--------
1. 单向遍历：用显式栈迭代（不用递归，避免深目录爆栈）。
2. 边走边聚合：每遇到一个文件，把大小累加到它自己以及所有祖先目录
   （祖先链随栈一起传递），一次遍历即可得到全部层级容量，
   不需要先把整棵树搬到内存/数据库里。
3. 只记录深度 <= track_depth 的目录节点，避免快照膨胀；
   但所有文件的字节数都会向上聚合，因此浅层目录的容量始终是真实值。
4. 跳过重解析点（junction / 符号链接）。Windows 上
   C:\\Users\\All Users、C:\\Documents and Settings、C:\\Windows\\WinSxS\\*
   等都是 reparse point，跟随会重复计数甚至死循环。
5. 硬链接按 (st_dev, st_ino) 去重 —— WinSxS 里几万个硬链接，
   不去重会凭空多算十几 GB，导致"总容量"和资源管理器对不上。
"""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, Sequence

FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

DEFAULT_BIG_FILE = 100 * 1024 * 1024
PROGRESS_EVERY = 2048  # 每处理多少个条目检查一次进度回调


# ---------------------------------------------------------------- 数据结构

@dataclass
class ScanStats:
    dirs: int = 0
    files: int = 0
    errors: int = 0
    skipped_reparse: int = 0
    hardlink_skipped: int = 0
    duration: float = 0.0
    disk_total: int = 0
    disk_free: int = 0

    @property
    def disk_used(self) -> int:
        return max(0, self.disk_total - self.disk_free)


@dataclass
class ScanResult:
    root: str
    started_at: str
    finished_at: str
    rows: list = field(default_factory=list)        # (path, depth, size, files)
    big_files: list = field(default_factory=list)   # (path, size, depth)
    stats: ScanStats = field(default_factory=ScanStats)

    @property
    def total_size(self) -> int:
        for path, depth, size, files in self.rows:
            if depth == 0:
                return size
        return 0


# ---------------------------------------------------------------- 辅助

def normalize_root(root: str) -> str:
    root = (root or "").strip().strip('"').strip("'")
    if not root:
        root = "C:\\"
    if len(root) == 2 and root[1] == ":":
        root += "\\"
    root = os.path.abspath(root)
    drive, tail = os.path.splitdrive(root)
    if not tail:
        root = drive + "\\"
    if not root.endswith("\\"):
        root += "\\"
    return root


def _xp(path: str) -> str:
    """加长路径前缀。"""
    if path.startswith("\\\\?\\"):
        return path
    if len(path) >= 2 and path[1] == ":":
        return "\\\\?\\" + path
    return path


def _glob_to_regex(pattern: str) -> str:
    """把 * / ? 通配符转成正则，* 只匹配单层（不含反斜杠）。"""
    out = ""
    for ch in pattern:
        if ch == "*":
            out += "[^\\\\]*"
        elif ch == "?":
            out += "[^\\\\]"
        else:
            out += re.escape(ch)
    return out


def compile_exclude(patterns: Sequence[str] | None):
    if not patterns:
        return None
    parts = [_glob_to_regex(p) for p in patterns if p]
    if not parts:
        return None
    return re.compile("(?i)(?:" + "|".join(parts) + ")$")


# ---------------------------------------------------------------- 扫描

def scan(
    root: str,
    track_depth: int = 5,
    big_file_bytes: int = DEFAULT_BIG_FILE,
    exclude: Sequence[str] | None = None,
    progress: Optional[Callable] = None,
    dedupe_hardlinks: bool = True,
) -> ScanResult:
    started_wall = datetime.now()
    t0 = time.perf_counter()
    root = normalize_root(root)
    ex = compile_exclude(exclude)

    stats = ScanStats()
    agg: dict[str, list] = {root: [0, 0, 0]}  # path -> [size, files, depth]
    big_files: list[tuple[str, int, int]] = []
    seen_links: set[tuple[int, int]] = set()

    # 栈元素：(用于访问的文件系统路径, 用于展示/存储的路径, 当前深度, 祖先链)
    # 祖先链是 ((depth, path), ...)，包含自身，长度 <= track_depth + 1
    stack: list[tuple[str, str, int, tuple]] = [(root, root, 0, ((0, root),))]
    counter = 0
    last_report = time.perf_counter()

    while stack:
        fs_path, disp, depth, chain = stack.pop()
        try:
            it = os.scandir(_xp(fs_path))
        except (PermissionError, FileNotFoundError):
            stats.errors += 1
            continue
        except OSError:
            stats.errors += 1
            continue

        sep = "" if disp.endswith("\\") else "\\"
        with it:
            while True:
                try:
                    entry = next(it)
                except StopIteration:
                    break
                except OSError:
                    stats.errors += 1
                    continue

                counter += 1
                if progress is not None and (counter % PROGRESS_EVERY) == 0:
                    now = time.perf_counter()
                    if now - last_report >= 0.4:
                        last_report = now
                        try:
                            progress(counter, stats, disp, agg[root][0])
                        except Exception:
                            pass

                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    stats.errors += 1
                    continue

                attrs = getattr(st, "st_file_attributes", 0)
                if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                    # junction / 符号链接 / 挂载点：跳过，避免重复与环路
                    stats.skipped_reparse += 1
                    continue

                child = disp + sep + entry.name

                if attrs & FILE_ATTRIBUTE_DIRECTORY:
                    if ex is not None and ex.match(child):
                        continue
                    stats.dirs += 1
                    cd = depth + 1
                    if cd <= track_depth:
                        agg.setdefault(child, [0, 0, cd])
                        chain2 = chain + ((cd, child),)
                    else:
                        chain2 = chain
                    stack.append((child, child, cd, chain2))
                    continue

                # 普通文件
                size = getattr(st, "st_size", 0) or 0
                if dedupe_hardlinks:
                    # DirEntry.stat() 在 Windows 上不填 st_nlink / st_ino / st_dev
                    # （恒为 0），必须走一次真正的 os.stat 才能拿到链接数。
                    try:
                        full = os.stat(_xp(child), follow_symlinks=False)
                        size = full.st_size
                        nlink = full.st_nlink
                        dev, ino = full.st_dev, full.st_ino
                    except OSError:
                        nlink, dev, ino = 0, 0, 0
                    if nlink > 1:
                        key = (dev, ino)
                        if key in seen_links:
                            stats.hardlink_skipped += 1
                            continue
                        seen_links.add(key)

                stats.files += 1
                for _, p in chain:
                    cell = agg[p]
                    cell[0] += size
                    cell[1] += 1

                if size >= big_file_bytes:
                    big_files.append((child, size, depth + 1))

    # 磁盘整体使用情况
    try:
        du = shutil.disk_usage(root)
        stats.disk_total = du.total
        stats.disk_free = du.free
    except OSError:
        pass

    stats.duration = time.perf_counter() - t0
    rows = [(p, v[2], v[0], v[1]) for p, v in agg.items()]
    rows.sort(key=lambda r: r[0])
    big_files.sort(key=lambda r: r[1], reverse=True)

    return ScanResult(
        root=root,
        started_at=started_wall.strftime("%Y-%m-%d %H:%M:%S"),
        finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        rows=rows,
        big_files=big_files,
        stats=stats,
    )
