"""命令行入口。"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__, analyze, config, report, scanner
from .store import Store
from .winutil import (fmt_size, is_admin, long_path, pad, relaunch_as_admin,
                      truncate_mid)

APP_DIR = Path(__file__).resolve().parent.parent
TASK_NAME = "DiskScope 磁盘空间扫描"


# ---------------------------------------------------------------- 输出辅助

def _c(text: str, color: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    codes = {"red": "31", "green": "32", "yellow": "33", "blue": "36", "gray": "90"}
    return f"\033[{codes.get(color, '0')}m{text}\033[0m"


def _signed_pad(v: int, width: int) -> str:
    """先按纯文本对齐，再着色，避免 ANSI 码破坏列宽。"""
    if v == 0:
        return pad("—", width, "right")
    s = ("+" if v > 0 else "-") + fmt_size(abs(v))
    return _c(pad(s, width, "right"), "red" if v > 0 else "green")


def _tw() -> int:
    try:
        return max(60, shutil.get_terminal_size((100, 20)).columns)
    except Exception:
        return 100


def _progress_factory(enabled: bool):
    if not enabled:
        return None
    state = {"last": 0.0}

    def cb(count, stats, cur, size):
        now = time.time()
        if now - state["last"] < 0.5:
            return
        state["last"] = now
        line = (f"\r  已扫描 {count:>8,} 项 | 文件 {stats.files:>8,} | "
                f"累计 {fmt_size(size):>10} | {truncate_mid(cur, 48)}")
        sys.stdout.write(line.ljust(110))
        sys.stdout.flush()

    return cb


def _print_header(title: str) -> None:
    print()
    print(_c("═" * 78, "gray"))
    print("  " + _c(title, "blue"))
    print(_c("═" * 78, "gray"))


# ---------------------------------------------------------------- scan

def cmd_scan(args, cfg) -> int:
    root = scanner.normalize_root(args.root or cfg.root())
    depth = args.depth if args.depth is not None else cfg.track_depth

    if not is_admin() and not args.no_elevate:
        print(_c("[!] 未以管理员身份运行，部分目录（System Volume Information、"
                 "其他用户目录等）将无法读取。", "yellow"))
        if relaunch_as_admin():
            print("已在新窗口以管理员身份重新启动…")
            return 0
        print(_c("[!] 提权失败，继续以普通权限扫描（结果会偏小）。", "yellow"))

    _print_header(f"扫描 {root}  ·  跟踪深度 {depth} 层"
                  + ("  ·  硬链接去重" if (cfg.dedupe_hardlinks and not args.fast) else "  ·  快速模式"))
    cb = _progress_factory(not args.quiet)
    t0 = time.time()
    result = scanner.scan(
        root=root,
        track_depth=depth,
        big_file_bytes=int(cfg.big_file_mb * 1024 * 1024),
        exclude=cfg.exclude or None,
        progress=cb,
        dedupe_hardlinks=cfg.dedupe_hardlinks and not args.fast,
    )
    if cb:
        sys.stdout.write("\r" + " " * 110 + "\r")

    store = Store(cfg.db_file())
    sid = store.add_snapshot(result, depth, note=args.note)
    if not args.keep_all:
        store.prune(root, cfg.keep_snapshots)

    st = result.stats
    print(f"  快照编号     #{sid}")
    print(f"  目录总量     {fmt_size(result.total_size)}"
          f"   （{st.files:,} 个文件 / {st.dirs:,} 个目录）")
    if st.disk_total:
        print(f"  磁盘已用     {fmt_size(st.disk_used)} / {fmt_size(st.disk_total)}"
              f"   剩余 {fmt_size(st.disk_free)}")
    print(f"  耗时         {st.duration:.1f}s   跳过链接 {st.skipped_reparse}"
          f"   硬链接去重 {st.hardlink_skipped}   无权限 {st.errors}")
    if result.total_size > st.disk_used * 1.02 and st.disk_total:
        print(_c("  注意：目录总量 > 磁盘已用，可能存在重复统计（跨卷挂载点）。", "yellow"))

    # 与上一次快照对比，给出即时提示
    snaps = store.snapshots(root)
    if len(snaps) >= 2:
        prev = snaps[-2]
        d = result.total_size - prev["total_size"]
        if d != 0:
            arrow = _c(f"+{fmt_size(d)}", "red") if d > 0 else _c(f"-{fmt_size(d)}", "green")
            print(f"  较上次       {arrow}   （上次 #{prev['id']} {prev['created_at']}）")
    store.close()

    _log_line(cfg, f"scan #{sid} root={root} size={result.total_size} "
                   f"files={st.files} duration={st.duration:.1f}s")

    if args.report:
        return _do_report(cfg, base_hours=cfg.base_hours, open_it=args.open,
                          target_id=sid, root=root)
    return 0


def _log_line(cfg, text: str) -> None:
    try:
        p = cfg.db_file().parent / "history.log"
        with p.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
    except Exception:
        pass


# ---------------------------------------------------------------- list

def cmd_list(args, cfg) -> int:
    store = Store(cfg.db_file())
    snaps = store.snapshots(args.root and scanner.normalize_root(args.root) or None)
    if not snaps:
        print("还没有任何快照，先运行：python -m diskscope scan")
        return 1
    _print_header("快照列表")
    head = (pad("ID", 6) + pad("时间", 21) + pad("目录总量", 12, "right")
            + pad("较上次", 12, "right") + pad("文件数", 10, "right")
            + pad("耗时", 8, "right") + "  根目录")
    print("  " + _c(head, "gray"))
    prev = None
    for s in snaps:
        dcell = pad("—", 12, "right") if prev is None else _signed_pad(s["total_size"] - prev["total_size"], 12)
        print("  " + pad(f"#{s['id']}", 6) + pad(s["created_at"], 21)
              + pad(fmt_size(s["total_size"]), 12, "right")
              + dcell
              + pad(f"{s['total_files']:,}", 10, "right")
              + pad(f"{s['duration']:.0f}s", 8, "right")
              + "  " + s["root"])
        prev = s
    print(f"\n  共 {len(snaps)} 个快照，数据库 {cfg.db_file()}")
    store.close()
    return 0


# ---------------------------------------------------------------- diff

def _resolve_target(store, args, cfg):
    root = scanner.normalize_root(args.root or cfg.root())
    snaps = store.snapshots(root)
    if not snaps:
        return None, None, None, []
    if args.id:
        tgt = store.snapshot(args.id)
        if not tgt:
            print(f"快照 #{args.id} 不存在")
            return None, None, None, []
        root = tgt["root"]
        snaps = store.snapshots(root)
    else:
        tgt = snaps[-1]
    if args.base_id:
        base = store.snapshot(args.base_id)
        if base is None:
            print(f"基线快照 #{args.base_id} 不存在（用 list 查看有效快照）")
    else:
        base = analyze.choose_baseline(snaps, tgt, args.hours)
    return tgt, base, root, snaps


def cmd_diff(args, cfg) -> int:
    store = Store(cfg.db_file())
    tgt, base, root, snaps = _resolve_target(store, args, cfg)
    if tgt is None:
        store.close()
        print("没有快照。先运行：python -m diskscope scan")
        return 1

    diff = analyze.compare(store, base["id"] if base else None, tgt["id"])
    if base and base["root"] != tgt["root"]:
        print(_c("  [!] 基线与目标快照根目录不同，对比结果没有意义。", "yellow"))
        store.close()
        return 1
    total_d = tgt["total_size"] - (base["total_size"] if base else 0)

    _print_header(f"空间变化  {root}")
    print(f"  当前快照  #{tgt['id']}  {tgt['created_at']}  总量 {fmt_size(tgt['total_size'])}")
    if args.base_id and base is None:
        store.close()
        return 1
    if base:
        print(f"  基线快照  #{base['id']}  {base['created_at']}  总量 {fmt_size(base['total_size'])}")
        print("  期间变化  " + _signed_pad(total_d, 12))
    else:
        print(_c("  仅有 1 个快照，无法对比。再扫描一次即可看到差异。", "yellow"))
        store.close()
        return 0

    rows = [r for r in diff.rows if r.own > 0]
    rows.sort(key=lambda r: r.own, reverse=True)
    rows = rows[: args.top]

    pw = max(30, _tw() - 4 - 13 - 13 - 2)
    print()
    print("  " + _c("增长归因 TOP（自身增长 = 该节点增长 − 直接子项增长）", "gray"))
    print("  " + _c(pad("#", 4) + pad("路径", pw) + pad("自身增长", 13, "right")
                   + pad("含子项", 13, "right"), "gray"))
    shown = 0
    for i, r in enumerate(rows, 1):
        if r.own < args.min_bytes:
            break
        shown += 1
        icon = "" if r.kind == "d" else "[F] "
        print("  " + pad(i, 4) + pad(truncate_mid(icon + r.path, pw), pw)
              + _signed_pad(r.own, 13) + _signed_pad(r.delta, 13))
    if not shown:
        print("  " + _c("  期间没有任何净增长。", "gray"))

    gone = sorted([r for r in diff.rows if r.delta < 0], key=lambda r: r.delta)
    if gone and args.top:
        print()
        print("  " + _c("释放空间 TOP", "gray"))
        print("  " + _c(pad("#", 4) + pad("路径", pw) + pad("变化", 13, "right"), "gray"))
        for i, r in enumerate(gone[: max(3, args.top // 3)], 1):
            print("  " + pad(i, 4) + pad(truncate_mid(r.path, pw), pw)
                  + _signed_pad(r.delta, 13))

    store.close()
    return 0


# ---------------------------------------------------------------- report

def _do_report(cfg, base_hours: float, open_it: bool, target_id=None,
               root: str | None = None, base_id: int | None = None) -> int:
    store = Store(cfg.db_file())
    root = scanner.normalize_root(root or cfg.root())
    snaps = store.snapshots(root)
    if not snaps:
        print("没有快照，先运行 scan")
        store.close()
        return 1
    tgt = store.snapshot(target_id) if target_id else snaps[-1]
    if tgt is None:
        tgt = snaps[-1]
    if base_id:
        base = store.snapshot(base_id)
        if base is None:
            print(_c(f"  快照 #{base_id} 不存在（用 list 查看有效快照）。", "red"))
            store.close()
            return 1
    else:
        base = None
    if base is None:
        base = analyze.choose_baseline(snaps, tgt, base_hours)
    if base and base["root"] != tgt["root"]:
        print(_c(f"  [!] 基线 #{base['id']} 与目标 #{tgt['id']} 根目录不同，无法对比。", "yellow"))
        store.close()
        return 1

    diff = analyze.compare(store, base["id"] if base else None, tgt["id"])
    base_nodes = store.nodes(base["id"]) if base else {}
    tgt_nodes = store.nodes(tgt["id"])
    spots = analyze.hotspots(base_nodes, tgt_nodes)

    path = report.write_report(cfg, diff, snaps, spots)
    print(f"  报告已生成  {path}")
    print(f"  快捷入口    {cfg.report_path() / 'latest.html'}")
    store.close()
    if open_it:
        report.open_report(path)
    return 0


def cmd_report(args, cfg) -> int:
    _print_header("生成 HTML 报告")
    return _do_report(cfg, args.hours, args.open, args.id, args.root,
                      getattr(args, "base_id", None))


# ---------------------------------------------------------------- 快照选择（菜单用）

def _pick_pair(cfg) -> tuple[int | None, int | None]:
    """交互式选择两个快照做对比（支持翻页浏览全部快照）。

    返回 (base_id, target_id)；target_id 为 None 表示放弃选择，走默认逻辑。
    输入格式：「基线ID 目标ID」或「目标ID」；n/p/f/l 翻页；直接回车走默认。
    """
    PAGE = 15
    store = Store(cfg.db_file())
    try:
        root = scanner.normalize_root(cfg.root())
        snaps = store.snapshots(root)
        if not snaps:
            print("  还没有任何快照。")
            return None, None

        total = len(snaps)
        pages = max(1, (total + PAGE - 1) // PAGE)
        page = pages - 1          # 默认停在最后一页（最近的快照）

        while True:
            begin = page * PAGE
            chunk = snaps[begin:begin + PAGE]
            print()
            print(f"  " + _c(f"快照列表  第 {page + 1}/{pages} 页 · 共 {total} 个"
                             f" · 本页 #{chunk[0]['id']}~#{chunk[-1]['id']}", "gray"))
            print("  " + _c(pad("ID", 7) + pad("时间", 21)
                            + pad("目录总量", 12, "right") + "  根目录", "gray"))
            for s in chunk:
                print("  " + pad(f"#{s['id']}", 7) + pad(s["created_at"], 21)
                      + pad(fmt_size(s["total_size"]), 12, "right") + "  " + s["root"])

            hint = "  " + _c("[n]下一页  [p]上一页  [f]最早  [l]最新  回车=默认对比", "gray")
            print(hint)
            raw = input("  对比哪两个？[基线ID 目标ID]（如 3 9）> ").strip()
            if not raw:
                return None, None
            cmd = raw.lower()
            if cmd in ("n", "next", "x", "+"):
                if page < pages - 1:
                    page += 1
                else:
                    print(_c("  已经是最后一页。", "yellow"))
                continue
            if cmd in ("p", "prev", "-"):
                if page > 0:
                    page -= 1
                else:
                    print(_c("  已经是第一页。", "yellow"))
                continue
            if cmd in ("f", "first"):
                page = 0
                continue
            if cmd in ("l", "last"):
                page = pages - 1
                continue

            parts = raw.replace(",", " ").replace("#", " ").split()
            try:
                ids = [int(x) for x in parts]
            except ValueError:
                print(_c("  输入无效（n/p/f/l 翻页，或输入快照 ID），请重试。", "yellow"))
                continue
            if len(ids) == 1:
                tgt_id = ids[0]
                base_id = None
            else:
                base_id, tgt_id = ids[-2], ids[-1]
            valid = {s["id"]: s for s in snaps}
            if tgt_id not in valid:
                print(_c(f"  快照 #{tgt_id} 不存在，请重试。", "yellow"))
                continue
            if base_id is not None:
                if base_id not in valid:
                    print(_c(f"  基线 #{base_id} 不存在，请重试。", "yellow"))
                    continue
                if base_id >= tgt_id:
                    print(_c("  基线必须早于目标（ID 更小），已自动交换。", "yellow"))
                    base_id, tgt_id = min(base_id, tgt_id), max(base_id, tgt_id)
                if valid[base_id]["root"] != valid[tgt_id]["root"]:
                    print(_c("  两个快照根目录不同，无法对比，请重选。", "yellow"))
                    continue
            return base_id, tgt_id
    finally:
        store.close()


# ---------------------------------------------------------------- top

def cmd_top(args, cfg) -> int:
    root = scanner.normalize_root(args.path or cfg.root())
    if not root.endswith("\\"):
        root += "\\"
    _print_header(f"实时分析  {root}  ·  深度 {args.depth}")
    if not is_admin():
        print(_c("  提示：非管理员运行，无权限的目录会被跳过（不影响其他目录的统计）。\n", "yellow"))
    result = scanner.scan(
        root=root, track_depth=args.depth,
        big_file_bytes=int(cfg.big_file_mb * 1024 * 1024),
        exclude=cfg.exclude or None,
        progress=_progress_factory(True),
        dedupe_hardlinks=cfg.dedupe_hardlinks,
    )
    sys.stdout.write("\r" + " " * 110 + "\r")
    rows = sorted(result.rows, key=lambda r: r[2], reverse=True)[: args.top]
    total = result.total_size
    print(f"  合计 {fmt_size(total)}，{result.stats.files:,} 文件\n")
    pw = max(30, _tw() - 4 - 12 - 9 - 11 - 2)
    print("  " + _c(pad("#", 4) + pad("目录", pw) + pad("容量", 12, "right")
                   + pad("占比", 9, "right") + pad("文件数", 11, "right"), "gray"))
    for i, (p, d, s, f) in enumerate(rows, 1):
        share = (s / total * 100) if total else 0
        print("  " + pad(i, 4) + pad(truncate_mid(p, pw), pw)
              + pad(fmt_size(s), 12, "right") + pad(f"{share:.1f}%", 9, "right")
              + pad(f"{f:,}", 11, "right"))
    return 0


# ---------------------------------------------------------------- prune

def cmd_prune(args, cfg) -> int:
    store = Store(cfg.db_file())
    n = store.prune(scanner.normalize_root(args.root) if args.root else None,
                    args.keep if args.keep is not None else cfg.keep_snapshots)
    print(f"  已清理 {n} 个旧快照，保留最近 {args.keep or cfg.keep_snapshots} 个")
    store.close()
    return 0


# ---------------------------------------------------------------- watch

def cmd_watch(args, cfg) -> int:
    root = scanner.normalize_root(args.root or cfg.root())
    interval = args.interval_min * 60
    if not is_admin():
        if relaunch_as_admin():
            return 0
    print(f"  监视 {root}，每 {args.interval_min} 分钟扫描一次，"
          f"增长超过 {cfg.growth_alert_gb} GB 时告警。Ctrl+C 退出。")
    store = Store(cfg.db_file())
    last_id = None
    try:
        while True:
            result = scanner.scan(root, cfg.track_depth,
                                  int(cfg.big_file_mb * 1024 * 1024),
                                  cfg.exclude or None,
                                  progress=_progress_factory(True),
                                  dedupe_hardlinks=cfg.dedupe_hardlinks)
            sys.stdout.write("\r" + " " * 110 + "\r")
            sid = store.add_snapshot(result, cfg.track_depth, note="watch")
            msg = f"[{datetime.now():%H:%M:%S}] #{sid} {fmt_size(result.total_size)}"
            if last_id:
                diff = analyze.compare(store, last_id, sid)
                d = result.total_size - store.snapshot(last_id)["total_size"]
                msg += f"  变化 {'+' if d >= 0 else ''}{fmt_size(d)}"
                top = [r for r in diff.rows if r.own > 0]
                top.sort(key=lambda r: r.own, reverse=True)
                if d >= cfg.growth_alert_gb * 1024 ** 3:
                    msg = _c("⚠ " + msg, "red")
                    for r in top[:5]:
                        msg += f"\n      + {fmt_size(r.own)}  {r.path}"
            print(msg)
            store.prune(root, cfg.keep_snapshots)
            last_id = sid
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  已停止。")
    finally:
        store.close()
    return 0


# ---------------------------------------------------------------- 计划任务

TASK_XML_DAILY = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>DiskScope 定期扫描磁盘目录容量并留存快照</Description>
    <URI>\\DiskScope</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>{days}</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT6H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python}</Command>
      <Arguments>"{launcher}" scan --quiet --report</Arguments>
      <WorkingDirectory>{cwd}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""

TASK_XML_HOURLY = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>DiskScope 定期扫描磁盘目录容量并留存快照</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT{hours}H</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT6H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python}</Command>
      <Arguments>"{launcher}" scan --quiet --report</Arguments>
      <WorkingDirectory>{cwd}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def cmd_install_task(args, cfg) -> int:
    if not is_admin():
        if relaunch_as_admin():
            return 0
        print("需要管理员权限才能创建 SYSTEM 计划任务。")
        return 1

    now = datetime.now()
    if args.at:
        hh, mm = (args.at.split(":") + ["00"])[:2]
        start_dt = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if start_dt < now:
            start_dt += timedelta(days=1)
    else:
        start_dt = (now + timedelta(minutes=2)).replace(second=0, microsecond=0)

    start = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
    launcher = str(APP_DIR / "run.py")
    if args.every_hours and args.every_hours > 0:
        xml = TASK_XML_HOURLY.format(hours=args.every_hours, start=start,
                                     python=sys.executable, cwd=str(APP_DIR),
                                     launcher=launcher)
        desc = f"每 {args.every_hours} 小时"
    else:
        xml = TASK_XML_DAILY.format(start=start, days=args.days,
                                    python=sys.executable, cwd=str(APP_DIR),
                                    launcher=launcher)
        desc = f"每 {args.days} 天 {start_dt:%H:%M}"

    xml_path = cfg.db_file().parent / "task.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(xml, encoding="utf-16")

    r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML",
                        str(xml_path), "/F"],
                       capture_output=True, text=True, encoding="gbk", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 and "成功" not in out and "SUCCESS" not in out.upper():
        print(_c("创建计划任务失败：", "red"))
        print(out.strip())
        print("\n可尝试：以管理员身份打开 PowerShell 手工执行")
        print(f'  schtasks /Create /TN "{TASK_NAME}" /XML "{xml_path}" /F')
        return 1

    print(_c("✓ 计划任务已安装", "green"))
    print(f"  任务名    {TASK_NAME}")
    print(f"  执行频率  {desc}")
    print(f"  运行身份  SYSTEM（最高权限，无需登录）")
    print(f"  命令      {sys.executable} \"{launcher}\" scan --quiet --report")
    print(f"  工作目录  {APP_DIR}")
    print(f"  立即试跑  schtasks /Run /TN \"{TASK_NAME}\"")
    print(f"  查看状态  schtasks /Query /TN \"{TASK_NAME}\" /V /FO LIST")
    return 0


def cmd_uninstall_task(args, cfg) -> int:
    if not is_admin():
        if relaunch_as_admin():
            return 0
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                       capture_output=True, text=True, encoding="gbk", errors="replace")
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    print(out or "(无输出)")
    return 0 if r.returncode == 0 else 1


# ---------------------------------------------------------------- 菜单

MENU = """
  ┌────────────────────────────────────────────────────────────┐
  │  DiskScope · C 盘空间监控与增长归因                        │
  ├────────────────────────────────────────────────────────────┤
  │  1  立即扫描并生成快照                                     │
  │  2  生成变化报告（可选任意两个快照对比）                  │
  │  3  控制台查看增长归因（可选任意两个快照对比）            │
  │  4  查看快照列表                                           │
  │  5  实时分析指定目录（不写库，类似 WizTree）               │
  │  6  安装定时扫描任务（每 6 小时，SYSTEM 权限）             │
  │  7  卸载定时扫描任务                                       │
  │  8  清理旧快照                                             │
  │  0  退出                                                   │
  └────────────────────────────────────────────────────────────┘"""


def cmd_menu(args, cfg) -> int:
    admin = is_admin()
    print(MENU)
    print(f"  权限状态：{'管理员 ✓' if admin else '普通用户（扫描将不完整）'}"
          f"   数据库：{cfg.db_file()}")
    while True:
        try:
            choice = input("\n  请选择 > ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice in ("0", "q", "Q", ""):
            return 0
        ns = argparse.Namespace(
            root=None, depth=None, note=None, quiet=False, keep_all=False,
            report=False, open=False, id=None, base_id=None, hours=cfg.base_hours,
            top=25, min_bytes=1024 ** 2, path=None, keep=None, at=None,
            every_hours=6, days=1, interval_min=30, fast=False, no_elevate=False,
        )
        try:
            if choice == "1":
                cmd_scan(ns, cfg)
            elif choice == "2":
                ns.open = True
                b, t = _pick_pair(cfg)
                if t is not None:
                    ns.id, ns.base_id = t, b
                cmd_report(ns, cfg)
            elif choice == "3":
                b, t = _pick_pair(cfg)
                if t is not None:
                    ns.id, ns.base_id = t, b
                cmd_diff(ns, cfg)
            elif choice == "4":
                cmd_list(ns, cfg)
            elif choice == "5":
                p = input("  目录路径（回车默认 C:\\）: ").strip() or "C:\\"
                ns.path = p
                ns.depth = int(input("  分析深度（回车默认 4）: ").strip() or 4)
                cmd_top(ns, cfg)
            elif choice == "6":
                ns.every_hours = int(input("  每隔几小时扫描一次（回车默认 6）: ").strip() or 6)
                cmd_install_task(ns, cfg)
            elif choice == "7":
                cmd_uninstall_task(ns, cfg)
            elif choice == "8":
                cmd_prune(ns, cfg)
            else:
                print("  无效选项")
        except KeyboardInterrupt:
            print("\n  已中断")


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="diskscope",
        description="DiskScope - Windows 磁盘空间监控与目录增长归因工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python -m diskscope scan --report --open        扫描 C 盘并生成报告
  python -m diskscope diff --hours 72 --top 30    对比 72 小时前的快照
  python -m diskscope diff --base-id 3 --id 9     任意两个快照对比
  python -m diskscope report --base-id 3 --id 9   用任意两快照出报告
  python -m diskscope top --path D:\\\\data --depth 4   实时分析某个目录
  python -m diskscope install-task --every-hours 6
""")
    p.add_argument("-v", "--version", action="version", version=f"DiskScope {__version__}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("scan", help="扫描并保存快照")
    s.add_argument("--root", help="扫描根目录，默认取配置")
    s.add_argument("--depth", type=int, help="跟踪深度，默认取配置(5)")
    s.add_argument("--note", help="备注")
    s.add_argument("--quiet", action="store_true", help="不显示进度（计划任务用）")
    s.add_argument("--keep-all", action="store_true", help="不自动清理旧快照")
    s.add_argument("--no-elevate", action="store_true", help="不自动提权")
    s.add_argument("--fast", action="store_true", help="快速模式：不做硬链接去重（快 3~6 倍，总量偏大）")
    s.add_argument("--report", action="store_true", help="扫描后立即生成报告")
    s.add_argument("--open", action="store_true", help="生成报告后打开浏览器")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("list", help="列出所有快照")
    s.add_argument("--root")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("diff", help="控制台输出增长归因")
    s.add_argument("--root")
    s.add_argument("--id", type=int, help="目标快照，默认最新")
    s.add_argument("--base-id", type=int,
                   help="基线快照 ID（任意早于目标的快照）；指定后忽略 --hours")
    s.add_argument("--hours", type=float, default=24.0, help="基线回溯小时数，默认 24")
    s.add_argument("--top", type=int, default=25, help="显示条数，默认 25")
    s.add_argument("--min-bytes", type=int, default=1024 ** 2, help="最小变化字节数")
    s.set_defaults(func=cmd_diff)

    s = sub.add_parser("report", help="生成 HTML 报告")
    s.add_argument("--root")
    s.add_argument("--id", type=int, help="目标快照，默认最新")
    s.add_argument("--base-id", type=int,
                   help="基线快照 ID（任意早于目标的快照）；指定后忽略 --hours")
    s.add_argument("--hours", type=float, default=24.0)
    s.add_argument("--open", action="store_true")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("top", help="实时分析指定目录容量分布")
    s.add_argument("--path", help="目录路径，默认配置根目录")
    s.add_argument("--depth", type=int, default=4)
    s.add_argument("--top", type=int, default=30)
    s.set_defaults(func=cmd_top)

    s = sub.add_parser("prune", help="清理旧快照")
    s.add_argument("--root")
    s.add_argument("--keep", type=int)
    s.set_defaults(func=cmd_prune)

    s = sub.add_parser("watch", help="常驻监视，定期扫描并告警")
    s.add_argument("--root")
    s.add_argument("--interval-min", type=int, default=30)
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("install-task", help="安装定时扫描计划任务")
    s.add_argument("--every-hours", type=int, default=0, help="每 N 小时运行一次")
    s.add_argument("--days", type=int, default=1, help="每 N 天运行一次")
    s.add_argument("--at", help="每天几点运行，如 12:30")
    s.set_defaults(func=cmd_install_task)

    s = sub.add_parser("uninstall-task", help="卸载计划任务")
    s.set_defaults(func=cmd_uninstall_task)

    s = sub.add_parser("menu", help="交互式菜单")
    s.set_defaults(func=cmd_menu)

    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        args = parser.parse_args(["menu"])

    cfg = config.load()
    try:
        return int(args.func(args, cfg) or 0)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(_c(f"\n[错误] {exc}", "red"))
        return 1
