"""DiskScope 冒烟测试。

在临时目录里构造一棵已知大小的目录树，把 scan / list / diff / report / top / prune
全部跑一遍，验证容量统计、硬链接去重、reparse 跳过、增长归因是否正确。

用法（在项目根目录）：
    python _tmp_menu_test.py
全部通过会打印 "SMOKE TEST OK"；失败会抛 AssertionError。

注意：本脚本只在系统临时目录里读写，不会碰你的 C 盘数据，也不会写项目内的数据库。
文件名沿用了创建时的临时命名，仅作自检用途，确认工具可用后可以直接删除。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from diskscope import analyze, cli, config, report, scanner  # noqa: E402
from diskscope.store import Store  # noqa: E402

MB = 1024 * 1024
TMP = os.environ.get("TEMP") or os.environ.get("TMP") or "."
ROOT = os.path.join(TMP, "ds_smoke_root")
DB = os.path.join(TMP, "ds_smoke.db")


def w(rel: str, size_mb: float) -> None:
    path = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * int(size_mb * MB))


def build_tree() -> None:
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT)
    for i in range(10):
        w(os.path.join("A", f"a{i}.bin"), 1)      # A = 10 MB
    for i in range(5):
        w(os.path.join("B", "C", f"c{i}.bin"), 2)  # B/C = 10 MB
    w(os.path.join("D", "d0.bin"), 3)             # D = 3 MB
    # 硬链接：与 A/a0.bin 同一份数据，去重后只应计一次
    try:
        os.link(os.path.join(ROOT, "A", "a0.bin"), os.path.join(ROOT, "B", "hard.bin"))
    except OSError:
        pass
    # 目录联接（reparse point）：必须被跳过，否则 B 会被算两次
    try:
        subprocess.run(["cmd", "/c", "mklink", "/J",
                        os.path.join(ROOT, "A", "link"), os.path.join(ROOT, "B")],
                       capture_output=True)
    except Exception:
        pass


def main() -> int:
    build_tree()

    # ---- 1. 扫描与容量正确性 ----
    r1 = scanner.scan(ROOT, track_depth=5, dedupe_hardlinks=True)
    assert r1.total_size == 23 * MB, f"总量应为 23MB，实际 {r1.total_size / MB:.1f}MB"
    assert r1.stats.skipped_reparse >= 1, "reparse point 未被跳过"
    print(f"[1] 扫描 23.0MB / 文件 {r1.stats.files} / 跳过链接 {r1.stats.skipped_reparse}"
          f" / 硬链接去重 {r1.stats.hardlink_skipped}   OK")

    # ---- 2. 制造增长后再扫 ----
    w(os.path.join("B", "C", "new.bin"), 4)
    w(os.path.join("E", "x.bin"), 1)
    shutil.rmtree(os.path.join(ROOT, "D"))
    r2 = scanner.scan(ROOT, track_depth=5, dedupe_hardlinks=True)
    assert r2.total_size == 25 * MB, f"总量应为 25MB，实际 {r2.total_size / MB:.1f}MB"
    print("[2] 增量扫描 25.0MB   OK")

    # ---- 3. 增长归因 ----
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(DB + suffix):
            os.remove(DB + suffix)
    store = Store(DB)
    id1 = store.add_snapshot(r1, 5)
    id2 = store.add_snapshot(r2, 5)
    diff = analyze.compare(store, id1, id2)
    own = {r.path: r.own for r in diff.rows}
    assert own[os.path.join(ROOT, "B", "C")] == 4 * MB, own
    assert own[os.path.join(ROOT, "E")] == 1 * MB, own
    assert own[os.path.join(ROOT, "D")] == -3 * MB, own
    assert own[os.path.join(ROOT, "B")] == 0, own
    assert own[os.path.join(ROOT, "A")] == 0, own
    print("[3] 归因：B\\C +4MB / E +1MB / D -3MB / B 自身 0   OK")

    # ---- 4. 报告 ----
    cfg = config.Config(roots=[ROOT], db_path=DB, report_dir=os.path.join(TMP, "ds_smoke_reports"))
    spots = analyze.hotspots(store.nodes(id1), store.nodes(id2))
    path = report.write_report(cfg, diff, store.snapshots(), spots)
    text = path.read_text(encoding="utf-8")
    for token in ("增长归因", "目录变化明细", "常见占用元凶体检", "容量趋势", "顶层目录分布"):
        assert token in text, f"报告缺少区块：{token}"
    assert "<html" in text and "</html>" in text
    print(f"[4] 报告 {path}  {len(text) / 1024:.1f} KB   OK")

    # ---- 5. 菜单里用到的各命令（Namespace 字段完整性）----
    ns = argparse.Namespace(
        root=None, depth=None, note=None, quiet=True, keep_all=False,
        report=True, open=False, id=None, base_id=None, hours=24.0,
        top=5, min_bytes=1, path=None, keep=3, at=None,
        every_hours=6, days=1, interval_min=30, fast=True, no_elevate=True,
    )
    for fn in (cli.cmd_scan, cli.cmd_list, cli.cmd_diff, cli.cmd_report, cli.cmd_prune):
        assert fn(ns, cfg) in (0, None), f"{fn.__name__} 返回非 0"
    ns.path, ns.depth = ROOT, 3
    assert cli.cmd_top(ns, cfg) in (0, None)
    print("[5] 菜单全部命令可执行   OK")

    store.close()
    print("\nSMOKE TEST OK  （临时数据在 %s，可自行删除）" % TMP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
