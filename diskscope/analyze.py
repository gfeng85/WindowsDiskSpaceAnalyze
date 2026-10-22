"""快照差值分析与增长归因。

"哪个目录的增长导致空间被占满"这个问题有两个层次：
  · 总增长  delta = target_size - base_size
  · 归因增长 own_delta = delta - Σ(直接子目录的 delta)

只看总增长会出现父子目录重复：C:\\Users\\x 涨了 5GB，如果全部来自
C:\\Users\\x\\AppData，两条各显示 5GB，看不出到底谁该负责。
own_delta 把子目录已经解释掉的部分扣走，剩下的就是"长在这个目录自己身上"的，
排序后即可一眼定位真正的增长源。
"""
from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .scanner import _glob_to_regex

NEW = "new"
GONE = "gone"
GREW = "grew"
SHRANK = "shrank"
SAME = "same"

STATUS_CN = {
    NEW: "新增",
    GONE: "消失",
    GREW: "增长",
    SHRANK: "减少",
    SAME: "不变",
}


@dataclass
class Row:
    path: str
    kind: str          # 'd' | 'f'
    base: int
    target: int
    delta: int
    depth: int
    status: str = SAME
    own: int = 0

    @property
    def is_dir(self) -> bool:
        return self.kind == "d"


@dataclass
class DiffResult:
    base: Optional[dict]
    target: dict
    rows: list = field(default_factory=list)

    def by_delta(self, limit: int | None = None) -> list[Row]:
        r = sorted(self.rows, key=lambda x: x.delta, reverse=True)
        return r[:limit] if limit else r

    def by_own(self, limit: int | None = None) -> list[Row]:
        r = sorted(self.rows, key=lambda x: x.own, reverse=True)
        return r[:limit] if limit else r

    def by_size(self, limit: int | None = None) -> list[Row]:
        r = sorted(self.rows, key=lambda x: x.target, reverse=True)
        return r[:limit] if limit else r


def _norm(p: str) -> str:
    """统一目录键：去掉结尾反斜杠，但盘根（C:\\）保留。"""
    if p.endswith("\\") and not (len(p) == 3 and p[1] == ":"):
        return p.rstrip("\\") or p
    return p


def _parent_key(path: str) -> Optional[str]:
    """取父目录的路径键，与 scanner 的路径写法保持一致。"""
    p = _norm(path)
    if "\\" not in p:
        return None
    parent = p.rsplit("\\", 1)[0]
    if parent.endswith(":"):
        parent += "\\"
    return _norm(parent)


def compare(store, base_id: Optional[int], target_id: int) -> DiffResult:
    base = store.nodes(base_id) if base_id else {}
    tgt = store.nodes(target_id)

    paths = set(base) | set(tgt)
    rows: list[Row] = []
    for p in paths:
        bs, _bf, bk, bd = base.get(p, (0, 0, "", 0))
        ts, _tf, tk, td = tgt.get(p, (0, 0, "", 0))
        delta = ts - bs
        if p not in base:
            status = NEW
        elif p not in tgt:
            status = GONE
        elif delta > 0:
            status = GREW
        elif delta < 0:
            status = SHRANK
        else:
            status = SAME
        kind = tk or bk or "d"
        depth = td if p in tgt else bd
        rows.append(
            Row(path=p, kind=kind, base=bs, target=ts, delta=delta, depth=depth, status=status)
        )

    # 归因：扣除直接子节点（子目录 + 记录下来的大文件）已解释的增长
    children: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        pk = _parent_key(r.path)
        if pk:
            children[pk].append(r)

    for r in rows:
        if r.kind != "d":
            r.own = r.delta
            continue
        kids = children.get(_norm(r.path))
        if not kids:
            r.own = r.delta
        else:
            r.own = r.delta - sum(k.delta for k in kids)

    return DiffResult(base=base_id and store.snapshot(base_id), target=store.snapshot(target_id) or {}, rows=rows)


# ---------------------------------------------------------------- 基线选择

def _parse_dt(s: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def choose_baseline(snapshots: list[dict], target: dict, hours: float = 24.0) -> Optional[dict]:
    """在 target 之前的快照里，挑最接近 target_time - hours 的那个。"""
    earlier = [s for s in snapshots if s["id"] < target["id"]]
    if not earlier:
        return None
    t = _parse_dt(target["created_at"])
    if t is None:
        return earlier[-1]
    want = t - timedelta(hours=hours)
    best = min(earlier, key=lambda s: abs(((_parse_dt(s["created_at"]) or t) - want).total_seconds()))
    return best


# ---------------------------------------------------------------- 已知元凶

# (名称, 路径模式, 说明/处置建议)
KNOWN_HOTSPOTS = [
    ("休眠文件 hiberfil.sys", "?:\\hiberfil.sys",
     "与物理内存等大的镜像文件。不用休眠可执行 powercfg /h off 直接释放"),
    ("虚拟内存 pagefile.sys", "?:\\pagefile.sys",
     "内存交换文件，大小≈物理内存。可在「系统属性-高级-虚拟内存」限制"),
    ("Windows 更新缓存", "?:\\Windows\\SoftwareDistribution\\Download",
     "补丁下载残留。停 wuauserv 后可清理"),
    ("组件存储 WinSxS", "?:\\Windows\\WinSxS",
     "含大量硬链接，实际占用远小于表面值，勿手工删。用 DISM StartComponentCleanup 清理"),
    ("Windows Installer 缓存", "?:\\Windows\\Installer",
     "MSI 补丁备份，原则上不可删"),
    ("Windows 临时目录", "?:\\Windows\\Temp", "系统临时文件，可定期清理"),
    ("内存转储 / 蓝屏日志", "?:\\Windows\\LiveKernelReports", "驱动异常产生的转储，可删"),
    ("Windows 错误报告", "?:\\ProgramData\\Microsoft\\Windows\\WER", "崩溃报告队列，可清"),
    ("Defender 特征库", "?:\\ProgramData\\Microsoft\\Windows Defender", "安全软件特征与扫描缓存"),
    ("Package Cache", "?:\\ProgramData\\Package Cache", "VS/VC 运行库安装包缓存"),
    ("卷影副本 / 系统还原", "?:\\System Volume Information",
     "还原点占用，用 vssadmin list shadowstorage 查看并限制配额"),
    ("回收站", "?:\\$Recycle.Bin", "清空回收站即可"),
    ("系统日志", "?:\\Windows\\System32\\winevt\\Logs", "事件日志，可用 wevtutil 清空"),
    ("字体缓存", "?:\\Windows\\ServiceProfiles\\LocalService\\AppData\\Local\\FontCache", "可删，自动重建"),
    ("用户临时目录", "?:\\Users\\*\\AppData\\Local\\Temp", "用户级临时文件，可清"),
    ("浏览器缓存", "?:\\Users\\*\\AppData\\Local\\Google\\Chrome\\User Data", "Chrome 配置与缓存"),
    ("浏览器缓存(Edge)", "?:\\Users\\*\\AppData\\Local\\Microsoft\\Edge\\User Data", "Edge 配置与缓存"),
    ("缩略图缓存", "?:\\Users\\*\\AppData\\Local\\Microsoft\\Windows\\Explorer", "thumbcache，可删"),
    ("崩溃转储", "?:\\Users\\*\\AppData\\Local\\CrashDumps", "应用崩溃转储，可删"),
    ("WSL 虚拟磁盘", "?:\\Users\\*\\AppData\\Local\\Packages",
     "WSL/Docker/商店应用数据，常见 ext4.vhdx 巨大"),
    ("Docker 数据", "?:\\Users\\*\\AppData\\Local\\Docker", "镜像层数据，docker system prune 清理"),
    ("pip 缓存", "?:\\Users\\*\\AppData\\Local\\pip\\Cache", "pip cache purge 清理"),
    ("npm 缓存", "?:\\Users\\*\\AppData\\Local\\npm-cache", "npm cache clean --force"),
    ("VSCode 缓存", "?:\\Users\\*\\AppData\\Roaming\\Code", "缓存与插件数据"),
    ("JetBrains 缓存", "?:\\Users\\*\\AppData\\Local\\JetBrains", "索引与日志，可清"),
    ("下载目录", "?:\\Users\\*\\Downloads", "常被忽视的堆积点"),
    ("OneDrive 本地副本", "?:\\Users\\*\\OneDrive", "云盘本地缓存"),
    ("Crashpad 上报", "?:\\Users\\*\\AppData\\Local\\Temp\\Crashpad", ""),
]


def match_pattern(pattern: str, nodes: dict) -> tuple[list[str], int]:
    """按模式匹配节点，只取最小深度的匹配项（避免父子重复累加）。"""
    rx = re.compile("(?i)^" + _glob_to_regex(pattern) + "$")
    hits = [(p, v[0], v[3]) for p, v in nodes.items() if rx.match(p)]
    if not hits:
        return [], 0
    dmin = min(h[2] for h in hits)
    picked = [h for h in hits if h[2] == dmin]
    return [h[0] for h in picked], sum(h[1] for h in picked)


@dataclass
class Hotspot:
    name: str
    pattern: str
    advice: str
    base: int = 0
    target: int = 0
    paths: list = field(default_factory=list)

    @property
    def delta(self) -> int:
        return self.target - self.base

    @property
    def found(self) -> bool:
        return self.target > 0 or self.base > 0


def hotspots(base_nodes: dict, target_nodes: dict) -> list[Hotspot]:
    out = []
    for name, pattern, advice in KNOWN_HOTSPOTS:
        paths, tsize = match_pattern(pattern, target_nodes)
        _, bsize = match_pattern(pattern, base_nodes)
        if tsize == 0 and bsize == 0:
            continue
        out.append(Hotspot(name, pattern, advice, bsize, tsize, paths))
    out.sort(key=lambda h: h.target, reverse=True)
    return out
