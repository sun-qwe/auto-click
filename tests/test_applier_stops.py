"""applier 的停止语义测试。

这里的每一条都对应一种"继续操作会放大账号风险"的场景，
必须验证脚本真的会停下来，而不是打日志继续。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.boss.applier import apply_to_jobs
from src.boss.models import CaptchaError, Job, LoginRequiredError, SelectorsBrokenError


def _cfg(**kw):
    base = dict(
        daily_cap=10, per_window_cap=5,
        min_interval_sec=[0, 0], page_interval_sec=[0, 0],
        stop_on_captcha=True, max_consecutive_failures=99,
        job_action_timeout_sec=5, dry_run=False, allow_unfiltered=False,
    )
    base.update(kw)
    return SimpleNamespace(schedule=SimpleNamespace(**{
        k: base[k] for k in ("daily_cap", "per_window_cap",
                             "min_interval_sec", "page_interval_sec")}),
        safety=SimpleNamespace(**{
            k: base[k] for k in ("stop_on_captcha", "max_consecutive_failures",
                                 "job_action_timeout_sec", "dry_run")}))


def _job(i=0):
    return Job(job_id=f"j{i}", title=f"Java{i}", company="X",
               salary="25-40K", city="深圳",
               url=f"https://www.zhipin.com/job_detail/j{i}.html")


@pytest.fixture
def state():
    st = MagicMock()
    st.remaining_today.return_value = 10
    return st


def test_stops_on_captcha(state):
    """验证码必须中止整轮，且向上抛出（不允许被吞掉变成'失败继续'）。"""
    session = MagicMock()
    exc = CaptchaError("出现滑块")

    def boom(*a, **kw):
        raise exc

    import src.boss.applier as mod
    original = mod._apply_one
    mod._apply_one = boom
    try:
        with pytest.raises(CaptchaError):
            apply_to_jobs(session, _cfg(), state, [_job(1), _job(2), _job(3)], print)
    finally:
        mod._apply_one = original


def test_stops_on_login_expired(state):
    """登录失效必须立刻停，不能对每个职位重复试探。"""
    session = MagicMock()
    exc = LoginRequiredError("登录失效")

    def boom(*a, **kw):
        raise exc

    import src.boss.applier as mod
    original = mod._apply_one
    mod._apply_one = boom
    try:
        with pytest.raises(LoginRequiredError):
            apply_to_jobs(session, _cfg(), state, [_job(1), _job(2)], print)
    finally:
        mod._apply_one = original


def test_stops_on_broken_selectors(state):
    """选择器失效（改版）立即停，不要重复重试同一个坏选择器。"""
    session = MagicMock()

    def boom(*a, **kw):
        raise SelectorsBrokenError("找不到按钮")

    import src.boss.applier as mod
    original = mod._apply_one
    mod._apply_one = boom
    try:
        with pytest.raises(SelectorsBrokenError):
            apply_to_jobs(session, _cfg(), state, [_job(1), _job(2)], print)
    finally:
        mod._apply_one = original


def test_stops_at_daily_cap(state):
    """额度耗尽必须停止，不多投一个。"""
    session = MagicMock()

    def ok(*a, **kw):
        return True, "已投递"

    import src.boss.applier as mod
    original = mod._apply_one
    mod._apply_one = ok
    try:
        cfg = _cfg(daily_cap=10, per_window_cap=2)
        stats = apply_to_jobs(session, cfg, state, [_job(i) for i in range(10)], print)
        assert stats.applied == 2
        assert "上限" in stats.stopped_reason
    finally:
        mod._apply_one = original


def test_stops_when_quota_exhausted(state):
    """今日额度已用完 -> 一个都不投。"""
    session = MagicMock()
    state.remaining_today.return_value = 0

    def ok(*a, **kw):
        return True, "已投递"

    import src.boss.applier as mod
    original = mod._apply_one
    mod._apply_one = ok
    try:
        stats = apply_to_jobs(session, _cfg(), state, [_job(1)], print)
        assert stats.applied == 0
        assert "额度" in stats.stopped_reason
    finally:
        mod._apply_one = original


def test_consecutive_failure_threshold(state):
    """连续失败达到阈值即停。"""
    session = MagicMock()

    def fail(*a, **kw):
        raise RuntimeError("网络抖动")

    import src.boss.applier as mod
    original = mod._apply_one
    mod._apply_one = fail
    try:
        cfg = _cfg(max_consecutive_failures=2)
        stats = apply_to_jobs(session, cfg, state, [_job(i) for i in range(10)], print)
        assert stats.failed == 2
        assert "连续失败" in stats.stopped_reason
    finally:
        mod._apply_one = original
