"""SQLite 快照存储。

两张表：
  snapshot —— 每次扫描一行，记录整体元信息
  node     —— 每个被跟踪的路径一行（目录或大文件），按路径做主键

差值分析就是两条 SQL 的 path 集合做 join，几千行规模毫秒级完成。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from .scanner import ScanResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL,
    root            TEXT    NOT NULL,
    total_size      INTEGER NOT NULL DEFAULT 0,
    total_files     INTEGER NOT NULL DEFAULT 0,
    total_dirs      INTEGER NOT NULL DEFAULT 0,
    errors          INTEGER NOT NULL DEFAULT 0,
    skipped_reparse INTEGER NOT NULL DEFAULT 0,
    hardlink_skipped INTEGER NOT NULL DEFAULT 0,
    duration        REAL    NOT NULL DEFAULT 0,
    disk_total      INTEGER NOT NULL DEFAULT 0,
    disk_free       INTEGER NOT NULL DEFAULT 0,
    track_depth     INTEGER NOT NULL DEFAULT 5,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS node (
    snapshot_id INTEGER NOT NULL,
    path        TEXT    NOT NULL,
    kind        TEXT    NOT NULL,   -- 'd' 目录 / 'f' 大文件
    depth       INTEGER NOT NULL,
    size        INTEGER NOT NULL,
    files       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_id, path)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_node_depth ON node(snapshot_id, depth);
"""

SNAP_FIELDS = (
    "id, created_at, root, total_size, total_files, total_dirs, errors, "
    "skipped_reparse, hardlink_skipped, duration, disk_total, disk_free, "
    "track_depth, note"
)


class Store:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=60)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------ 写入

    def add_snapshot(self, result: ScanResult, track_depth: int, note: str | None = None) -> int:
        st = result.stats
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO snapshot(created_at, root, total_size, total_files, "
                "total_dirs, errors, skipped_reparse, hardlink_skipped, duration, "
                "disk_total, disk_free, track_depth, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    result.finished_at,
                    result.root,
                    result.total_size,
                    st.files,
                    st.dirs,
                    st.errors,
                    st.skipped_reparse,
                    st.hardlink_skipped,
                    round(st.duration, 3),
                    st.disk_total,
                    st.disk_free,
                    track_depth,
                    note,
                ),
            )
            sid = int(cur.lastrowid)

            payload = [
                (sid, path, "d", depth, size, files)
                for path, depth, size, files in result.rows
            ]
            payload.extend(
                (sid, path, "f", max(depth, 1), size, 0)
                for path, size, depth in result.big_files
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO node(snapshot_id, path, kind, depth, size, files) "
                "VALUES (?,?,?,?,?,?)",
                payload,
            )
        return sid

    # ------------------------------------------------------------ 读取

    def snapshots(self, root: str | None = None) -> list[dict]:
        sql = f"SELECT {SNAP_FIELDS} FROM snapshot"
        args: tuple = ()
        if root:
            sql += " WHERE root = ?"
            args = (root,)
        sql += " ORDER BY id ASC"
        cur = self.conn.execute(sql, args)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def snapshot(self, sid: int) -> Optional[dict]:
        cur = self.conn.execute(f"SELECT {SNAP_FIELDS} FROM snapshot WHERE id = ?", (sid,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, row))

    def latest(self, root: str | None = None) -> Optional[dict]:
        snaps = self.snapshots(root)
        return snaps[-1] if snaps else None

    def nodes(self, sid: int) -> dict[str, tuple[int, int, str, int]]:
        """返回 {path: (size, files, kind, depth)}。"""
        cur = self.conn.execute(
            "SELECT path, size, files, kind, depth FROM node WHERE snapshot_id = ?", (sid,)
        )
        return {r[0]: (r[1], r[2], r[3], r[4]) for r in cur}

    def node_count(self, sid: int) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM node WHERE snapshot_id = ?", (sid,))
        return int(cur.fetchone()[0])

    # ------------------------------------------------------------ 维护

    def prune(self, root: str | None = None, keep: int = 400) -> int:
        snaps = self.snapshots(root)
        if len(snaps) <= keep:
            return 0
        drop = [s["id"] for s in snaps[: len(snaps) - keep]]
        with self.conn:
            self.conn.executemany("DELETE FROM node WHERE snapshot_id = ?", [(i,) for i in drop])
            self.conn.executemany("DELETE FROM snapshot WHERE id = ?", [(i,) for i in drop])
        return len(drop)

    def delete_snapshot(self, sid: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM node WHERE snapshot_id = ?", (sid,))
            self.conn.execute("DELETE FROM snapshot WHERE id = ?", (sid,))

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
