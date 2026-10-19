"""state 去重与幂等性单测。

这里的语义直接决定"会不会重复骚扰同一个 HR"，必须逐条锁死。
"""

import time

import pytest

from src.boss.models import Job
from src.boss.state import State


@pytest.fixture
def state(tmp_path):
    st = State(tmp_path / "test.db")
    yield st
    st.close()


def j(job_id="j1", **kw):
    base = dict(job_id=job_id, title="Java", company="X", salary="20-35K", city="深圳")
    base.update(kw)
    return Job(**base)


def test_applied_ids_empty(state):
    assert state.applied_ids() == frozenset()


def test_mark_applied_prevents_resend(state):
    state.mark_attempting(j())
    # attempting 期间也不允许重投：宁可漏投不可重复
    assert "j1" in state.applied_ids()


def test_attempting_then_applied(state):
    state.mark_attempting(j())
    state.mark_applied(j())
    assert "j1" in state.applied_ids()
    assert state.today_applied_count() == 1


def test_failed_can_retry(state):
    """failed 是"确定没投出去"，允许下一轮重试。"""
    state.mark_attempting(j())
    state.mark_failed(j(), "超时")
    assert "j1" not in state.applied_ids()


def test_resend_is_idempotent(state):
    """同一职位重复调用两次，DB 里仍只有一条 applying 状态。"""
    state.mark_attempting(j())
    state.mark_attempting(j())
    rows = state.conn.execute(
        "SELECT COUNT(*) c FROM jobs WHERE job_id='j1'").fetchone()
    assert rows["c"] == 1


def test_daily_counter(state):
    for i in range(3):
        state.mark_attempting(j(f"j{i}"))
        state.mark_applied(j(f"j{i}"))
    assert state.today_applied_count() == 3
    assert state.remaining_today(10) == 7
    assert state.remaining_today(2) == 0      # 超额不返回负数


def test_record_skip_does_not_block(state):
    state.record_skip(j(), "薪资不足")
    assert "j1" not in state.applied_ids()


def test_seen_then_can_apply(state):
    state.record_seen(j())
    assert "j1" not in state.applied_ids()
    state.mark_attempting(j())
    state.mark_applied(j())
    assert state.today_applied_count() == 1


def test_run_lifecycle(state):
    from src.boss.models import RunStats
    rid = state.start_run(dry_run=True)
    stats = RunStats(collected=5, applied=2, skipped=3, dry_run=True)
    state.finish_run(rid, stats)
    row = state.conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "ok"
    assert row["collected"] == 5
    assert row["dry_run"] == 1


def test_run_records_error(state):
    from src.boss.models import RunStats
    rid = state.start_run()
    state.finish_run(rid, RunStats(), error="遇到验证码")
    row = state.conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "error"
    assert row["error"] == "遇到验证码"


def test_recent_applied_ordering(state):
    for i in range(3):
        state.mark_attempting(j(f"j{i}"))
        state.mark_applied(j(f"j{i}"))
    rows = state.recent_applied(2)
    assert len(rows) == 2
    assert rows[0]["job_id"] == "j2"      # 最新在前


def test_summary(state):
    state.mark_attempting(j())
    state.mark_applied(j())
    s = state.stats_summary()
    assert s["total_applied"] == 1
    assert s["today"] == 1
