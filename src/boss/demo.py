"""生成一份日报样例，用于在没有 BOSS 账号的情况下确认报告长什么样。

    python -m src.boss.cli demo-report
"""

from __future__ import annotations

from datetime import datetime

from .models import ApplyRecord, Job, RunResult, RunStats, SkipRecord
from .state import CST


def _demo_result() -> RunResult:
    jobs_applied = [
        Job(job_id="d1", title="Java开发工程师", company="星辰科技",
            salary="25-40K", city="深圳",
            url="https://www.zhipin.com/job_detail/demo1.html"),
        Job(job_id="d2", title="后端开发工程师", company="云图网络",
            salary="30-50K", city="深圳",
            url="https://www.zhipin.com/job_detail/demo2.html"),
        Job(job_id="d3", title="Golang开发工程师", company="海纳数据",
            salary="20-35K", city="深圳",
            url="https://www.zhipin.com/job_detail/demo3.html"),
    ]
    jobs_skipped = [
        (Job(job_id="s1", title="Java外包岗", company="某人力",
             salary="18-25K", city="深圳"), "排除词命中「外包」"),
        (Job(job_id="s2", title="Java开发工程师", company="小作坊网络",
             salary="12-18K", city="深圳"), "薪资下限 12.0K 低于要求 20.0K"),
        (Job(job_id="s3", title="前端开发工程师", company="某某科技",
             salary="25-45K", city="深圳"), "标题未命中关键词"),
        (Job(job_id="s4", title="Java开发工程师", company="远方科技",
             salary="30-50K", city="北京"), "城市 北京 不在白名单内"),
    ]
    r = RunResult(stats=RunStats(
        collected=7, applied=3, skipped=4, failed=0, dry_run=True,
        stopped_reason="达到本时间窗上限",
        started_at=datetime.now(CST).isoformat(timespec="seconds"),
        finished_at=datetime.now(CST).isoformat(timespec="seconds"),
    ))
    r.applied = [ApplyRecord(job=j, success=True, message="干跑（未实际投递）")
                 for j in jobs_applied]
    r.skipped = [SkipRecord(job=j, reason=reason) for j, reason in jobs_skipped]
    return r


def build_demo_html() -> str:
    from types import SimpleNamespace
    from .reporter import render_html

    class _DemoState:
        def stats_summary(self):
            return {"total_applied": 27, "today": 3, "seen": 11}

    cfg = SimpleNamespace(root=None)
    return render_html(_demo_result(), cfg, _DemoState())
