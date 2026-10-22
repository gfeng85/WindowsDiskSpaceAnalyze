"""配置管理。

配置保存在程序目录下的 diskscope.json，首次运行自动生成。
所有相对路径都相对于程序目录解析，便于整体拷贝迁移。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "diskscope.json"
DEFAULT_DB = "data/diskscope.db"
DEFAULT_REPORT_DIR = "reports"


@dataclass
class Config:
    # 监控的根目录（可多个，scan 默认取第一个）
    roots: list = field(default_factory=lambda: ["C:\\"])
    # 跟踪深度：C:\ = 0，C:\Users = 1，C:\Users\x = 2 ... 默认 5
    track_depth: int = 5
    # 单个文件超过该大小才单独记录（MB）
    big_file_mb: float = 100.0
    # 数据库路径（相对程序目录）
    db_path: str = DEFAULT_DB
    # 保留最近多少个快照
    keep_snapshots: int = 400
    # 报告输出目录
    report_dir: str = DEFAULT_REPORT_DIR
    # 排除的目录（支持 * 通配单层），默认排除网络挂载类噪声
    exclude: list = field(default_factory=list)
    # 增长告警阈值（GB），watch 模式使用
    growth_alert_gb: float = 5.0
    # 硬链接去重：更准确（WinSxS 等目录含大量硬链接），但每个文件多一次 stat，
    # 全盘扫描会慢 3~6 倍。关闭后总量会明显偏大（实测偏 6% 左右）。
    dedupe_hardlinks: bool = True
    # 默认基线回溯小时数：报告默认和「多少小时前」的快照比较
    base_hours: float = 24.0
    # 目录变化明细最多写入多少行
    max_detail_rows: int = 1500
    # 目录变化明细的最小变化量（MB），低于该值视为抖动不展示
    detail_min_mb: float = 1.0

    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else APP_DIR / p

    def report_path(self) -> Path:
        p = Path(self.report_dir)
        return p if p.is_absolute() else APP_DIR / p

    def root(self) -> str:
        return self.roots[0] if self.roots else "C:\\"


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        cfg = Config()
        save(cfg, path)
        return cfg
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return Config()
    cfg = Config()
    for k, v in raw.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8"
    )
