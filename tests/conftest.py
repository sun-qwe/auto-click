"""共享测试夹具。"""

import pytest

from src.boss.models import Job
from src.boss.state import State


def rules(**kw):
    """构造 Rules 替身，避免测试依赖真实配置文件。"""
    from types import SimpleNamespace
    base = dict(
        title_any=[],
        exclude_keywords=[],
        company_blacklist=[],
        min_salary_k=0,
        cities_allow=[],
        skip_if_applied_before=True,
        allow_unfiltered=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def sample_state(tmp_path):
    st = State(tmp_path / "e2e.db")
    yield st
    st.close()


@pytest.fixture
def job_factory():
    def make(job_id="j1", **kw):
        base = dict(job_id=job_id, title="Java开发工程师", company="某科技",
                    salary="25-40K", city="深圳", url="")
        base.update(kw)
        return Job(**base)
    return make
