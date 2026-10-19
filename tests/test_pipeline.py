"""端到端冒烟：用假职位验证「采集→筛选→额度→日报」整条链路。

不碰真实 BOSS，不启动浏览器。目的是在没有账号的情况下也能验证
编排逻辑正确 —— 尤其是额度限制和跳过原因能不能传到日报里。
"""

import pytest

from src.boss.filters import screen
from src.boss.models import ApplyRecord, Job, RunResult, RunStats, SkipRecord
from src.boss.reporter import render_html
from src.boss.state import State
from tests.conftest import rules


def _jobs(n: int) -> list[Job]:
    out = []
    for i in range(n):
        out.append(Job(
            job_id=f"fake{i}",
            title="Java开发工程师" if i % 3 else "销售专员",
            company="某科技" if i % 5 else "某某人力",
            salary="25-40K" if i % 2 else "15-20K",
            city="深圳" if i % 4 else "北京",
            url=f"https://www.zhipin.com/job_detail/fake{i}.html",
        ))
    return out


def test_pipeline_builds_report(tmp_path, sample_state):
    cfg = _cfg(tmp_path)
    jobs = _jobs(10)

    passed, skipped = screen(jobs, cfg.rules, sample_state.applied_ids())
    assert passed, "应当有职位通过筛选"

    result = RunResult(stats=RunStats(collected=len(jobs), applied=0, dry_run=True))
    result.applied = [ApplyRecord(job=j, success=True, message="干跑") for j in passed]
    result.skipped = [SkipRecord(job=j, reason=r) for j, r in skipped]

    html = render_html(result, cfg, sample_state)
    assert "BOSS" in html
    assert "销售专员" in html          # 跳过明细应该出现在报告里
    assert "跳过明细" in html
    assert "干跑模式" in html          # 干跑必须有醒目标记


def test_daily_cap_respected(tmp_path, sample_state):
    """额度耗尽时必须停止，且 state 里计数准确。"""
    cfg = _cfg(tmp_path)
    cap = cfg.schedule.daily_cap

    passed, _ = screen(_jobs(50), cfg.rules, sample_state.applied_ids())
    sent = 0
    for job in passed:
        if sample_state.remaining_today(cap) <= 0:
            break
        sample_state.mark_attempting(job)
        sample_state.mark_applied(job)
        sent += 1
    assert sent == cap
    assert sample_state.remaining_today(cap) == 0


def test_no_resend_across_runs(tmp_path):
    """第二轮运行看到同样的职位，不能重复投递。"""
    st = State(tmp_path / "s.db")
    cfg = _cfg(tmp_path)
    jobs = _jobs(5)

    first, _ = screen(jobs, cfg.rules, st.applied_ids())
    for j in first:
        st.mark_attempting(j)
        st.mark_applied(j)

    second, second_skipped = screen(jobs, cfg.rules, st.applied_ids())
    assert second == [], "已投递过的职位不应再次通过"

    # 第二轮里，凡第一轮投过的，必须全部死在"已投递过"这一关，
    # 而不是因为别的规则被跳过（那样说明去重没生效）
    reasons = {j.job_id: r for j, r in second_skipped}
    for j in first:
        assert reasons.get(j.job_id) == "已投递过", (
            f"{j.job_id} 第二轮跳过原因不是『已投递过』而是 {reasons.get(j.job_id)!r}"
        )
    st.close()


def _cfg(tmp_path):
    from types import SimpleNamespace
    return SimpleNamespace(
        rules=rules(title_any=["Java"], min_salary_k=20,
                    cities_allow=["深圳"], exclude_keywords=["外包"],
                    company_blacklist=["人力"]),
        schedule=SimpleNamespace(daily_cap=3, per_window_cap=2),
        report=SimpleNamespace(output_dir=str(tmp_path / "reports"), keep_days=30),
        root=tmp_path,
        path=lambda p: tmp_path / p,
    )
