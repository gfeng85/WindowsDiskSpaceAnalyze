"""HTML 报告生成（单文件、零外部依赖）。"""
from __future__ import annotations

import html
import os
import platform
import socket
import webbrowser
from datetime import datetime
from pathlib import Path

from . import __version__
from .analyze import DiffResult, Hotspot, Row, STATUS_CN
from .winutil import fmt_size

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f2f4f7;color:#0f172a;
  font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;}
.wrap{max-width:1220px;margin:0 auto;padding:28px 20px 64px;}
h1{font-size:22px;margin:0 0 6px;font-weight:650;letter-spacing:.2px}
h2{font-size:15px;margin:0 0 14px;font-weight:650;color:#0f172a;
  display:flex;align-items:center;gap:8px}
h2 .tag{font-size:11px;font-weight:500;color:#64748b;background:#eef2f7;
  border:1px solid #e2e8f0;border-radius:20px;padding:1px 9px}
.sub{color:#64748b;font-size:12.5px;margin:0}
.card{background:#fff;border:1px solid #e4e8ee;border-radius:12px;
  padding:18px 20px;margin:16px 0;box-shadow:0 1px 2px rgba(16,24,40,.04)}
header{padding:6px 2px 14px;border-bottom:1px solid #e4e8ee;margin-bottom:4px}
.meta{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:12px;font-size:12.5px;color:#475569}
.meta b{color:#0f172a;font-weight:600}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0}
.stat{background:#fff;border:1px solid #e4e8ee;border-radius:12px;padding:15px 17px}
.stat .k{font-size:12px;color:#64748b;margin-bottom:6px}
.stat .v{font-size:23px;font-weight:650;letter-spacing:-.4px;font-variant-numeric:tabular-nums}
.stat .d{font-size:11.5px;color:#94a3b8;margin-top:4px}
.up{color:#cf3b3b}.down{color:#0f8a68}.neutral{color:#0f172a}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:7px 9px;text-align:left;border-bottom:1px solid #eef1f5;vertical-align:middle}
th{font-size:11.5px;font-weight:600;color:#64748b;background:#fafbfc;position:sticky;top:0;
  white-space:nowrap;cursor:pointer;user-select:none}
th:hover{color:#2563eb}
th.num,td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tbody tr:hover{background:#f7f9fc}
.mono{font-family:ui-monospace,"Cascadia Mono",Consolas,monospace;font-size:12px}
.path{word-break:break-all;line-height:1.45}
.bar{height:5px;border-radius:3px;background:#eef1f5;overflow:hidden;min-width:70px}
.bar>i{display:block;height:100%;border-radius:3px}
.pill{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;
  border:1px solid transparent;white-space:nowrap}
.p-grew{background:#fdeeee;color:#c0392b;border-color:#f6d5d1}
.p-new{background:#fdf1e3;color:#b45309;border-color:#f3ddc0}
.p-shrank{background:#e9f7f1;color:#0f8a68;border-color:#cdeadf}
.p-gone{background:#eef2f7;color:#475569;border-color:#e2e8f0}
.p-same{background:#f6f7f9;color:#94a3b8;border-color:#eceff3}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
input[type=search]{border:1px solid #d8dee7;border-radius:8px;padding:6px 11px;
  font-size:12.5px;width:260px;outline:none;background:#fff;color:#0f172a}
input[type=search]:focus{border-color:#93b4f7;box-shadow:0 0 0 3px rgba(37,99,235,.09)}
.btn{border:1px solid #d8dee7;background:#fff;border-radius:8px;padding:6px 12px;
  font-size:12.5px;cursor:pointer;color:#334155}
.btn:hover{border-color:#93b4f7;color:#2563eb}
.scroll{max-height:560px;overflow:auto;border:1px solid #eef1f5;border-radius:10px}
.empty{color:#94a3b8;font-size:12.5px;padding:14px 0}
.hint-note{font-size:12px;color:#64748b;margin-top:10px;line-height:1.7}
code{background:#f1f4f8;border:1px solid #e6eaf0;border-radius:5px;padding:1px 6px;
  font-family:ui-monospace,Consolas,monospace;font-size:11.8px;color:#334155}
pre{background:#0f172a;color:#e2e8f0;border-radius:10px;padding:14px 16px;overflow:auto;
  font-family:ui-monospace,Consolas,monospace;font-size:12px;line-height:1.75;margin:0}
footer{margin-top:26px;color:#94a3b8;font-size:11.5px;text-align:center;line-height:1.8}
.legend{display:flex;gap:16px;font-size:11.5px;color:#64748b;margin-top:8px;flex-wrap:wrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}
@media(max-width:860px){.stats{grid-template-columns:repeat(2,1fr)}}
"""

JS = """
function dscSort(th){
  const tb=th.closest('table'), idx=th.cellIndex;
  const asc = th.dataset.asc!=='1'; th.dataset.asc = asc?'1':'0';
  const rows=[...tb.tBodies[0].rows];
  rows.sort((a,b)=>{
    const x=a.cells[idx].dataset.v??a.cells[idx].innerText;
    const y=b.cells[idx].dataset.v??b.cells[idx].innerText;
    const nx=parseFloat(x), ny=parseFloat(y);
    if(!isNaN(nx)&&!isNaN(ny)&&x!==''&&y!=='') return asc?nx-ny:ny-nx;
    return asc? String(x).localeCompare(String(y),'zh'): String(y).localeCompare(String(x),'zh');
  });
  rows.forEach(r=>tb.tBodies[0].appendChild(r));
}
function dscFilter(inp,id){
  const q=inp.value.trim().toLowerCase();
  document.querySelectorAll('#'+id+' tbody tr').forEach(tr=>{
    tr.style.display = (!q || tr.dataset.path.toLowerCase().includes(q)) ? '' : 'none';
  });
}
function dscResort(id,col){
  const th=document.querySelector('#'+id+' th:nth-child('+col+')');
  if(!th) return;
  th.dataset.asc='0';
  dscSort(th);
}
"""


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _pct(v: float, peak: float) -> float:
    if peak <= 0:
        return 0.0
    return max(1.5, min(100.0, abs(v) / peak * 100))


def _status_pill(status: str) -> str:
    cls = {"grew": "p-grew", "new": "p-new", "shrank": "p-shrank",
           "gone": "p-gone", "same": "p-same"}.get(status, "p-same")
    return f'<span class="pill {cls}">{STATUS_CN.get(status, status)}</span>'


def _trend_svg(snaps: list[dict]) -> str:
    if len(snaps) < 2:
        return '<div class="empty">至少需要 2 次快照才能绘制趋势线（当前 1 次）。安装定时任务后会逐日累积。</div>'
    pts = [(s["created_at"][5:16], s["total_size"]) for s in snaps][-40:]
    n = len(pts)
    vals = [p[1] for p in pts]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1
    W, H = 1120, 190
    l, r, t, b = 62, 14, 16, 30
    iw, ih = W - l - r, H - t - b

    def X(i):
        return l + (iw * i / (n - 1))

    def Y(v):
        return t + ih - ih * (v - lo) / (hi - lo)

    coords = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(pts))
    area = f"{l:.1f},{t+ih:.1f} " + coords + f" {X(n-1):.1f},{t+ih:.1f}"

    grid = []
    for k in range(4):
        v = lo + (hi - lo) * k / 3
        y = Y(v)
        grid.append(
            f'<line x1="{l}" y1="{y:.1f}" x2="{W-r}" y2="{y:.1f}" stroke="#eef1f5" stroke-width="1"/>'
            f'<text x="{l-8}" y="{y+4:.1f}" text-anchor="end" font-size="10.5" fill="#94a3b8">{_e(fmt_size(v))}</text>'
        )
    dots = "".join(
        f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3" fill="#2563eb">'
        f"<title>{_e(pts[i][0])}  {_e(fmt_size(v))}</title></circle>"
        for i, (_, v) in enumerate(pts)
    )
    labels = ""
    step = max(1, n // 6)
    for i in range(0, n, step):
        labels += (f'<text x="{X(i):.1f}" y="{H-10}" text-anchor="middle" '
                   f'font-size="10.5" fill="#94a3b8">{_e(pts[i][0])}</text>')

    return f"""<svg viewBox="0 0 {W} {H}" width="100%" height="{H}" role="img">
<defs><linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#2563eb" stop-opacity=".20"/>
<stop offset="100%" stop-color="#2563eb" stop-opacity="0"/>
</linearGradient></defs>
{''.join(grid)}
<polygon points="{area}" fill="url(#g1)"/>
<polyline points="{coords}" fill="none" stroke="#2563eb" stroke-width="2"
  stroke-linejoin="round" stroke-linecap="round"/>
{dots}{labels}
</svg>"""


def _detail_table(rows: list[Row], max_rows: int, min_bytes: int = 1024 * 1024) -> str:
    cands = sorted(rows, key=lambda r: abs(r.delta), reverse=True)
    rows = [r for r in cands if abs(r.delta) >= min_bytes]
    if len(rows) < 30:
        rows = cands[:100]
    rows = rows[:max_rows]
    if not rows or (rows and rows[0].delta == 0):
        return '<div class="empty">两次快照之间没有任何变化。</div>'
    peak = max((abs(r.delta) for r in rows), default=1) or 1
    body = []
    for r in rows:
        color = "#cf3b3b" if r.delta > 0 else ("#0f8a68" if r.delta < 0 else "#cbd5e1")
        icon = "📁" if r.is_dir else "📄"
        body.append(
            f'<tr data-path="{_e(r.path)}">'
            f'<td class="path mono">{icon} {_e(r.path)}</td>'
            f'<td class="num" data-v="{r.depth}">{r.depth}</td>'
            f'<td class="num" data-v="{r.base}">{_e(fmt_size(r.base))}</td>'
            f'<td class="num" data-v="{r.target}">{_e(fmt_size(r.target))}</td>'
            f'<td class="num" data-v="{r.delta}" style="color:{color};font-weight:600">'
            f'{_e(("+" if r.delta > 0 else "") + fmt_size(r.delta) if r.delta else "—")}'
            f'<div class="bar"><i style="width:{_pct(r.delta, peak):.1f}%;background:{color}"></i></div></td>'
            f'<td class="num" data-v="{r.own}">{_e(("+" if r.own > 0 else "") + fmt_size(r.own) if r.own else "—")}</td>'
            f'<td>{_status_pill(r.status)}</td></tr>'
        )
    note = (f'<div class="hint-note">仅列出变化量 ≥ {fmt_size(min_bytes)} 的条目，'
            f'最多 {max_rows} 条；更小的抖动可忽略，完整数据保存在 SQLite 的 node 表。</div>')
    return f"""<div class="scroll"><table id="tbl-detail">
<thead><tr>
<th onclick="dscSort(this)">路径</th>
<th class="num" onclick="dscSort(this)">深度</th>
<th class="num" onclick="dscSort(this)">基线</th>
<th class="num" onclick="dscSort(this)">当前</th>
<th class="num" onclick="dscSort(this)">变化</th>
<th class="num" onclick="dscSort(this)">归因</th>
<th onclick="dscSort(this)">状态</th>
</tr></thead><tbody>{''.join(body)}</tbody></table></div>{note}"""


def _own_table(rows: list[Row], limit: int = 15) -> str:
    rows = [r for r in sorted(rows, key=lambda x: x.own, reverse=True) if r.own > 0][:limit]
    if not rows:
        return '<div class="empty">没有发现任何净增长。</div>'
    peak = max(r.own for r in rows) or 1
    body = []
    for i, r in enumerate(rows, 1):
        icon = "📁" if r.is_dir else "📄"
        sub = f"+{_e(fmt_size(r.delta))}" if r.is_dir else "—"
        body.append(
            f'<tr data-path="{_e(r.path)}">'
            f'<td class="num" style="color:#94a3b8">{i}</td>'
            f'<td class="path mono">{icon} {_e(r.path)}</td>'
            f'<td class="num" style="color:#cf3b3b;font-weight:600">+{_e(fmt_size(r.own))}'
            f'<div class="bar"><i style="width:{_pct(r.own, peak):.1f}%;background:linear-gradient(90deg,#f0803c,#cf3b3b)"></i></div></td>'
            f'<td class="num" style="color:#94a3b8">{sub}</td>'
            f'<td>{_status_pill(r.status)}</td></tr>'
        )
    return f"""<div class="scroll" style="max-height:520px"><table>
<thead><tr><th style="width:44px">#</th><th>增长来源</th>
<th class="num" style="width:230px">自身增长（归因）</th>
<th class="num" style="width:120px">含子项</th><th style="width:70px">状态</th></tr></thead>
<tbody>{''.join(body)}</tbody></table></div>
<div class="hint-note">「自身增长」= 该节点总增长 − 其直接子项（子目录与已记录的大文件）总增长，
即无法再向下拆解的净增量，通常就是真正的元凶。加粗排序后，逐层下钻即可定位到具体文件。</div>"""


def _size_rank_table(rows: list[Row], limit: int = 15) -> str:
    """无基线时用：目录容量排行。"""
    rows = [r for r in sorted(rows, key=lambda x: x.target, reverse=True)
            if r.kind == "d"][:limit]
    if not rows:
        return '<div class="empty">没有采集到目录数据。</div>'
    peak = rows[0].target or 1
    grand = max((r.target for r in rows), default=1) or 1
    body = []
    for i, r in enumerate(rows, 1):
        share = r.target / grand * 100
        body.append(
            f'<tr data-path="{_e(r.path)}">'
            f'<td class="num" style="color:#94a3b8">{i}</td>'
            f'<td class="path mono">📁 {_e(r.path)}</td>'
            f'<td class="num" style="font-weight:600">{_e(fmt_size(r.target))}'
            f'<div class="bar"><i style="width:{_pct(r.target, peak):.1f}%;background:#93b4f7"></i></div></td>'
            f'<td class="num" style="color:#64748b">{share:.1f}%</td>'
            f'<td class="num" style="color:#94a3b8">{r.depth}</td></tr>'
        )
    return f"""<div class="scroll" style="max-height:520px"><table>
<thead><tr><th style="width:44px">#</th><th>目录</th>
<th class="num" style="width:240px">容量</th>
<th class="num" style="width:90px">占总量比</th>
<th class="num" style="width:60px">深度</th></tr></thead>
<tbody>{''.join(body)}</tbody></table></div>
<div class="hint-note">当前只有 1 个快照，无法计算增长。下一次扫描后本区块会自动切换成
「增长归因」，直接指出是哪个目录、哪个文件导致的增长。</div>"""


def _hotspot_table(spots: list[Hotspot]) -> str:
    if not spots:
        return '<div class="empty">未命中任何已知占用热点（可能跟踪深度不足，或路径不在跟踪范围内）。</div>'
    peak = max((h.target for h in spots), default=1) or 1
    body = []
    for h in spots:
        d = h.delta
        dcolor = "#cf3b3b" if d > 0 else ("#0f8a68" if d < 0 else "#94a3b8")
        dtext = ("+" if d > 0 else "") + fmt_size(d) if d else "—"
        body.append(
            f'<tr data-path="{_e(h.name)} {_e(h.pattern)}">'
            f'<td>{_e(h.name)}</td>'
            f'<td class="path mono">{_e(h.paths[0] if h.paths else h.pattern)}'
            f'{" 等 %d 处" % len(h.paths) if len(h.paths) > 1 else ""}</td>'
            f'<td class="num" data-v="{h.target}">{_e(fmt_size(h.target))}'
            f'<div class="bar"><i style="width:{h.target/peak*100:.1f}%;background:#93b4f7"></i></div></td>'
            f'<td class="num" style="color:{dcolor};font-weight:600">{_e(dtext)}</td>'
            f'<td style="color:#64748b;font-size:11.8px">{_e(h.advice)}</td></tr>'
        )
    return f"""<div class="scroll" style="max-height:460px"><table id="tbl-hot">
<thead><tr><th style="width:180px">热点</th><th>路径</th>
<th class="num" style="width:150px">当前大小</th>
<th class="num" style="width:110px">期间变化</th>
<th style="width:38%">说明 / 处置</th></tr></thead>
<tbody>{''.join(body)}</tbody></table></div>"""


def _big_file_table(rows: list[Row], cfg, limit: int = 20) -> str:
    files = [r for r in rows if r.kind == "f"]
    if not files:
        return ('<div class="empty">没有超过阈值的单文件记录（阈值见配置 big_file_mb）。'
                '但请注意 pagefile.sys / hiberfil.sys 这类根目录文件，'
                '若显示缺失通常是无权限读取大小。</div>')
    changed = [r for r in files if r.delta != 0]
    if changed:
        top_now = sorted(changed, key=lambda r: abs(r.delta), reverse=True)[:limit]
        caption = "按变化量排序"
    else:
        top_now = sorted(files, key=lambda r: r.target, reverse=True)[:limit]
        caption = "本次无变化，按大小排序"
    peak = max((r.target for r in top_now), default=1) or 1
    body = []
    for r in top_now:
        d = r.delta
        dcolor = "#cf3b3b" if d > 0 else ("#0f8a68" if d < 0 else "#94a3b8")
        dtext = ("+" if d > 0 else "") + fmt_size(d) if d else "—"
        body.append(
            f'<tr data-path="{_e(r.path)}">'
            f'<td class="path mono">{_e(r.path)}</td>'
            f'<td class="num" data-v="{r.target}">{_e(fmt_size(r.target))}'
            f'<div class="bar"><i style="width:{r.target/peak*100:.1f}%;background:#a5b4fc"></i></div></td>'
            f'<td class="num" style="color:{dcolor};font-weight:600">{_e(dtext)}</td>'
            f'<td>{_status_pill(r.status)}</td></tr>'
        )
    return f"""<div class="scroll"><table>
<thead><tr><th>文件</th><th class="num" style="width:200px">大小</th>
<th class="num" style="width:120px">期间变化</th><th style="width:70px">状态</th></tr></thead>
<tbody>{''.join(body)}</tbody></table></div>
<div class="hint-note">{caption}。仅收录 ≥ {getattr(cfg, 'big_file_mb', 100)} MB 的文件。</div>"""


ADVICE = """# 1) 卷影副本 / 系统还原占用（目录扫描常常读不到 System Volume Information）
vssadmin list shadowstorage

# 2) 组件存储真实占用与清理（安全，可回收数 GB）
Dism /Online /Cleanup-Image /StartComponentCleanup /ResetBase

# 3) 关闭休眠，直接回收一个与内存等大的 hiberfil.sys
powercfg /h off

# 4) Windows 更新缓存清理
net stop wuauserv & net stop bits
rd /s /q C:\\Windows\\SoftwareDistribution\\Download
net start wuauserv & net start bits

# 5) 查看回收站 / 各用户目录真实占用（PowerShell）
Get-ChildItem C:\\Users -Directory | ForEach-Object {
  $s = (Get-ChildItem $_.FullName -Recurse -Force -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum
  '{0,-30} {1,10:N2} GB' -f $_.Name, ($s/1GB)
}"""


RESORT_BTN = (
    '<button class="btn" onclick="dscResort(\'tbl-detail\',5)">按变化量重排</button>'
)


def build_html(
    cfg,
    diff: DiffResult,
    snaps: list[dict],
    spot_list: list[Hotspot],
) -> str:
    tgt = diff.target or {}
    base = diff.base or {}
    rows = diff.rows

    total = tgt.get("total_size", 0)
    delta = total - base.get("total_size", 0) if base else 0
    used = max(0, tgt.get("disk_total", 0) - tgt.get("disk_free", 0))
    free = tgt.get("disk_free", 0)
    dtotal = tgt.get("disk_total", 0)

    base_time = base.get("created_at", "无基线")
    span = ""
    if base and tgt:
        span = f"{base_time} → {tgt.get('created_at', '')}"
    else:
        span = f"{tgt.get('created_at', '')}（首次快照，无对比基线）"

    delta_cls = "up" if delta > 0 else ("down" if delta < 0 else "neutral")
    delta_txt = ("+" if delta > 0 else "") + fmt_size(delta) if delta else "0"

    total_dirs = sum(1 for r in rows if r.kind == "d")
    grew = sum(1 for r in rows if r.delta > 0)
    shrunk = sum(1 for r in rows if r.delta < 0)
    has_base = bool(base)

    delta_lbl = "期间目录总量变化" if has_base else "目录总量"
    delta_show = delta_txt if has_base else _e(fmt_size(total))
    delta_sub = span if has_base else "首次快照，暂无可对比基线"
    nodes_val = (f"{grew} <span style=\"font-size:14px;color:#94a3b8\">增</span> "
                 f"{shrunk} <span style=\"font-size:14px;color:#94a3b8\">减</span>") \
        if has_base else "首次采集"
    own_title = ('增长归因 <span class="tag">谁该负责</span>' if has_base
                 else '目录容量排行 <span class="tag">首次快照</span>')
    own_body = _own_table(rows) if has_base else _size_rank_table(rows)

    cards = f"""
<div class="stats">
  <div class="stat"><div class="k">所在卷已用</div>
    <div class="v">{_e(fmt_size(used))}</div>
    <div class="d">卷容量 {_e(fmt_size(dtotal))} · 剩余 {_e(fmt_size(free))}</div></div>
  <div class="stat"><div class="k">{delta_lbl}</div>
    <div class="v {delta_cls}">{delta_show}</div>
    <div class="d">{_e(delta_sub)}</div></div>
  <div class="stat"><div class="k">变化节点</div>
    <div class="v">{nodes_val}</div>
    <div class="d">跟踪目录 {total_dirs:,} 个 · 文件 {tgt.get('total_files', 0):,} 个</div></div>
  <div class="stat"><div class="k">扫描耗时</div>
    <div class="v">{tgt.get('duration', 0):.1f}s</div>
    <div class="d">跳过链接 {tgt.get('skipped_reparse', 0)} · 无权限 {tgt.get('errors', 0)}</div></div>
</div>"""

    # 按层级汇总，给一个"各顶层目录现状"
    top = [r for r in rows if r.depth == 1 and r.kind == "d"]
    top.sort(key=lambda r: r.target, reverse=True)
    peak = max((r.target for r in top), default=1) or 1
    top_rows = "".join(
        f'<tr><td class="path mono">{_e(r.path)}</td>'
        f'<td class="num">{_e(fmt_size(r.target))}'
        f'<div class="bar"><i style="width:{r.target/peak*100:.1f}%;background:#93b4f7"></i></div></td>'
        f'<td class="num" style="color:{"#cf3b3b" if r.delta>0 else ("#0f8a68" if r.delta<0 else "#94a3b8")};font-weight:600">'
        f'{_e((("+" if r.delta > 0 else "") + fmt_size(r.delta)) if r.delta else "—")}</td>'
        f'<td>{_status_pill(r.status)}</td></tr>'
        for r in top[:12]
    )

    gen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    host = socket.gethostname()

    if len(snaps) >= 2:
        lo = min((s["total_size"] for s in snaps), default=0)
        hi = max((s["total_size"] for s in snaps), default=0)
        legend_html = (
            '<div class="legend"><span><i class="dot" style="background:#2563eb"></i>'
            '目录总量（已按硬链接去重）</span><span>纵轴：'
            f'{_e(fmt_size(lo))} ~ {_e(fmt_size(hi))}</span></div>'
        )
    else:
        legend_html = ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DiskScope · {_e(tgt.get('root', 'C:\\'))} 空间变化报告</title>
<style>{CSS}</style></head><body><div class="wrap">

<header>
  <h1>DiskScope · 磁盘空间变化报告</h1>
  <p class="sub">对比基线快照，定位到底是哪个目录吃掉了空间</p>
  <div class="meta">
    <span>扫描根目录 <b>{_e(tgt.get('root', ''))}</b></span>
    <span>当前快照 <b>#{tgt.get('id', '-')}</b> · {_e(tgt.get('created_at', ''))}</span>
    <span>基线快照 <b>{'#' + str(base.get('id')) if base else '无'}</b> · {_e(base_time)}</span>
    <span>跟踪深度 <b>{tgt.get('track_depth', '-')}</b> 层</span>
    <span>主机 <b>{_e(host)}</b> · {_e(platform.release())}</span>
  </div>
</header>

{cards}

<div class="card">
  <h2>容量趋势 <span class="tag">最近 {min(len(snaps), 40)} 次快照</span></h2>
  {_trend_svg(snaps)}
  {legend_html}
</div>

<div class="card">
  <h2>{own_title}</h2>
  {own_body}
</div>

<div class="card">
  <h2>顶层目录分布 <span class="tag">扫描根的一级子目录</span></h2>
  <div class="scroll" style="max-height:420px"><table>
  <thead><tr><th>目录</th><th class="num" style="width:230px">当前容量</th>
  <th class="num" style="width:120px">期间变化</th><th style="width:70px">状态</th></tr></thead>
  <tbody>{top_rows}</tbody></table></div>
</div>

<div class="card">
  <h2>目录变化明细 <span class="tag">权重排序 · 可搜索</span></h2>
  <div class="toolbar">
    <input type="search" placeholder="输入关键字过滤路径，例如 AppData / Temp / vhdx"
      oninput="dscFilter(this,'tbl-detail')">
    {RESORT_BTN}
  </div>
  {_detail_table(rows, getattr(cfg, 'max_detail_rows', 1500),
                 int(getattr(cfg, 'detail_min_mb', 1.0) * 1024 * 1024))}
</div>

<div class="card">
  <h2>大文件变化 <span class="tag">≥ {getattr(cfg, 'big_file_mb', 100)} MB</span></h2>
  {_big_file_table(rows, cfg)}
</div>

<div class="card">
  <h2>常见占用元凶体检 <span class="tag">自动命中</span></h2>
  {_hotspot_table(spot_list)}
</div>

<div class="card">
  <h2>目录扫描覆盖不到的地方 <span class="tag">建议手动核查</span></h2>
  <p class="hint-note" style="margin-top:0;margin-bottom:12px">
  以下项目不属于普通目录，全盘遍历拿不到准确值，需要单独命令核查：
  卷影副本（系统还原）、回收站、休眠/页面文件、被独占锁定的日志文件。
  这也是"目录加起来不等于已用空间"的常见原因。</p>
  <pre>{_e(ADVICE)}</pre>
</div>

<footer>
  DiskScope v{__version__} · 生成于 {gen} · 数据来源：{_e(str(cfg.db_file()))}<br>
  目录总量与磁盘已用存在差异属于正常现象：前者只统计可访问的文件，且已对硬链接去重。
</footer>
</div>
<script>{JS}</script>
</body></html>"""


def write_report(cfg, diff: DiffResult, snaps: list[dict], spot_list: list[Hotspot]) -> Path:
    out_dir = cfg.report_path()
    out_dir.mkdir(parents=True, exist_ok=True)
    html_text = build_html(cfg, diff, snaps, spot_list)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    detail = out_dir / f"report-{stamp}.html"
    latest = out_dir / "latest.html"
    detail.write_text(html_text, encoding="utf-8")
    latest.write_text(html_text, encoding="utf-8")
    return detail


def open_report(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        webbrowser.open(path.as_uri())
