"""SQLite 状态存储：去重 / 每日计数 / 运行记录。

这一层是"崩了重跑也不重复投递"的唯一保证。设计要点：

1. jobs 表以 job_id 主键 + UNIQUE 约束，重复插入被数据库拒绝，
   而不是靠应用层判断（应用层判断有竞态）。
2. 投递动作先写 status='attempting'，成功后再置 'applied'。
   崩在中间的记录保持 attempting，下一轮**不会重投**（宁可漏投也不重复投）。
3. 每日计数用单独的 applied_events 表按本地日期聚合，
   不用 COUNT(*) 全表扫。
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 中国时区，日报里的"今天"以此为准
CST = timezone(timedelta(hours=8))

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    company     TEXT NOT NULL DEFAULT '',
    salary      TEXT NOT NULL DEFAULT '',
    city        TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'seen',   -- seen | attempting | applied | skipped | failed
    reason      TEXT NOT NULL DEFAULT '',
    first_seen  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applied_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    day         TEXT NOT NULL,                  -- YYYY-MM-DD (CST)
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    collected   INTEGER NOT NULL DEFAULT 0,
    applied     INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    dry_run     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'running',
    error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_applied_events_day ON applied_events(day);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


class State:
    """对 SQLite 的薄封装。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        # WAL 让崩溃后数据不丢
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "State":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -----------------------------------------------------------------
    # 去重
    # -----------------------------------------------------------------
    def applied_ids(self) -> frozenset[str]:
        """历史上已投递 / 正在投递的 job_id。

        attempting 也算在内：宁可这一轮漏掉，也不能重复触达 HR。
        """
        rows = self.conn.execute(
            "SELECT job_id FROM jobs WHERE status IN ('applied', 'attempting')"
        ).fetchall()
        return frozenset(r["job_id"] for r in rows)

    def record_seen(self, job) -> None:
        """记录一个见过的职位。已存在则忽略。"""
        now = _now()
        self.conn.execute(
            """INSERT OR IGNORE INTO jobs
               (job_id, title, company, salary, city, url, status, first_seen, updated_at)
               VALUES (?,?,?,?,?,?,'seen',?,?)""",
            (job.job_id, job.title, job.company, job.salary,
             job.city, job.url, now, now),
        )

    def record_skip(self, job, reason: str) -> None:
        now = _now()
        self.conn.execute(
            """INSERT INTO jobs (job_id,title,company,salary,city,url,status,reason,first_seen,updated_at)
               VALUES (?,?,?,?,?,?,'skipped',?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 status='skipped', reason=excluded.reason, updated_at=excluded.updated_at""",
            (job.job_id, job.title, job.company, job.salary,
             job.city, job.url, reason, now, now),
        )
        self.conn.commit()

    def mark_attempting(self, job) -> None:
        """投递前先落 attempting。这是幂等性的关键写入。"""
        now = _now()
        self.conn.execute(
            """INSERT INTO jobs (job_id,title,company,salary,city,url,status,first_seen,updated_at)
               VALUES (?,?,?,?,?,?,'attempting',?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 status='attempting', updated_at=excluded.updated_at""",
            (job.job_id, job.title, job.company, job.salary,
             job.city, job.url, now, now),
        )
        self.conn.commit()

    def mark_applied(self, job) -> None:
        now = _now()
        self.conn.execute(
            "UPDATE jobs SET status='applied', updated_at=? WHERE job_id=?",
            (now, job.job_id),
        )
        self.conn.execute(
            "INSERT INTO applied_events (job_id, day, applied_at) VALUES (?,?,?)",
            (job.job_id, today(), now),
        )
        self.conn.commit()

    def mark_failed(self, job, reason: str = "") -> None:
        """投递失败：回退成 seen，下一轮可以重试。

        和 attempting 不同：attempting 表示"可能已经投出去了"，
        failed 表示"确定没投出去"，所以这里允许重试。
        """
        now = _now()
        self.conn.execute(
            "UPDATE jobs SET status='failed', reason=?, updated_at=? WHERE job_id=?",
            (reason, now, job.job_id),
        )
        self.conn.commit()

    # -----------------------------------------------------------------
    # 额度
    # -----------------------------------------------------------------
    def today_applied_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM applied_events WHERE day=?", (today(),)
        ).fetchone()
        return int(row["c"])

    def remaining_today(self, daily_cap: int) -> int:
        return max(0, daily_cap - self.today_applied_count())

    # -----------------------------------------------------------------
    # 运行记录
    # -----------------------------------------------------------------
    def start_run(self, dry_run: bool = True) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, dry_run, status) VALUES (?,?,'running')",
            (_now(), int(dry_run)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, stats, error: str | None = None) -> None:
        self.conn.execute(
            """UPDATE runs SET finished_at=?, collected=?, applied=?, skipped=?, failed=?,
               dry_run=?, status=?, error=? WHERE id=?""",
            (_now(), stats.collected, stats.applied, stats.skipped,
             stats.failed, int(stats.dry_run),
             "error" if error else "ok", error, run_id),
        )
        self.conn.commit()

    def recent_applied(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT j.job_id,j.title,j.company,j.salary,j.city,j.url,e.applied_at
               FROM applied_events e JOIN jobs j ON j.job_id=e.job_id
               ORDER BY e.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    def stats_summary(self) -> dict:
        total_applied = self.conn.execute(
            "SELECT COUNT(*) c FROM applied_events").fetchone()["c"]
        today_count = self.today_applied_count()
        seen = self.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE status='seen'").fetchone()["c"]
        return {"total_applied": total_applied, "today": today_count, "seen": seen}


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")
