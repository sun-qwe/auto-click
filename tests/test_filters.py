"""filters 模块单测 —— 表驱动，穷举规则分支。

这是本项目最高价值的测试：筛选逻辑错了 = 海投 = 账号风控 + 简历被标记低质量。
"""

from types import SimpleNamespace

import pytest

from src.boss.filters import evaluate, screen
from src.boss.models import Job


def rules(**kw):
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


def job(**kw):
    base = dict(job_id="j1", title="Java开发工程师", company="某科技", salary="20-35K",
                city="深圳", tags=(), url="")
    base.update(kw)
    return Job(**base)


# ---------------------------------------------------------------------------
# 通过路径
# ---------------------------------------------------------------------------

def test_pass_when_no_rules():
    assert evaluate(job(), rules()).passed


@pytest.mark.parametrize("title,expect", [
    ("Java开发工程师", True),
    ("Golang后端", True),
    ("Go开发", True),
    ("服务端工程师", True),
    ("前端开发工程师", False),
    ("销售专员", False),
])
def test_title_any(title, expect):
    r = rules(title_any=["Java", "Go", "后端", "服务端"])
    assert evaluate(job(title=title), r).passed is expect


def test_skip_if_applied():
    r = rules(skip_if_applied_before=True)
    assert not evaluate(job(), r, frozenset({"j1"})).passed
    assert evaluate(job(), r, frozenset({"other"})).passed


def test_applied_ignored_when_disabled():
    r = rules(skip_if_applied_before=False)
    assert evaluate(job(), r, frozenset({"j1"})).passed


# ---------------------------------------------------------------------------
# 拒绝路径 + 原因文案
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("company", ["某某人力外包", "XX人力资源有限公司"])
def test_company_blacklist(company):
    v = evaluate(job(company=company), rules(company_blacklist=["人力"]))
    assert not v.passed
    assert "黑名单" in v.reason and "人力" in v.reason


@pytest.mark.parametrize("kwargs", [
    dict(company="某某外包"),
    dict(title="Java外包岗"),
    dict(tags=("驻场",)),
])
def test_exclude_keywords(kwargs):
    v = evaluate(job(**kwargs), rules(exclude_keywords=["外包", "外派", "驻场"]))
    assert not v.passed
    assert "排除词" in v.reason


@pytest.mark.parametrize("salary,expect", [
    ("20-35K", True),
    ("15-25K", False),      # 下限低于 20
    ("18k-30k", False),
    ("25-40K·13薪", True),
    ("面议", True),          # 解析不出来 -> 放行，避免误杀好职位
    ("", True),
])
def test_min_salary(salary, expect):
    v = evaluate(job(salary=salary), rules(min_salary_k=20))
    assert v.passed is expect
    if not expect:
        assert "薪资" in v.reason


@pytest.mark.parametrize("city,expect", [
    ("深圳", True),
    ("广州", True),
    ("北京", False),
])
def test_cities_allow(city, expect):
    v = evaluate(job(city=city), rules(cities_allow=["深圳", "广州"]))
    assert v.passed is expect


def test_blacklist_wins_over_title():
    """黑名单优先级高于关键词 —— 安全侧优先。"""
    v = evaluate(job(company="ABC外包", title="Java"),
                 rules(title_any=["Java"], exclude_keywords=["外包"]))
    assert not v.passed
    assert "排除词" in v.reason


def test_empty_needles_never_kill():
    """配置里留了空字符串不能导致全部职位被误杀。"""
    assert evaluate(job(), rules(exclude_keywords=["", "外包"])).passed
    assert evaluate(job(), rules(company_blacklist=[""])).passed


def test_every_rejection_has_reason():
    """任何拒绝都必须带原因 —— 日报要能逐条解释。"""
    # 每条规则各配一个能被它拒绝的职位，确保拒绝路径真的被走到
    for kwargs, target in [
        (dict(company_blacklist=["科技"]), job()),
        (dict(exclude_keywords=["外包"]), job(title="Java外包")),
        (dict(title_any=["Python"]), job()),
        (dict(min_salary_k=99), job()),
        (dict(cities_allow=["上海"]), job()),
    ]:
        v = evaluate(target, rules(**kwargs))
        assert not v.passed, f"{kwargs} 未能拒绝 {target.title}"
        assert v.reason.strip(), f"{kwargs} 的拒绝没有原因"


# ---------------------------------------------------------------------------
# 批量筛选
# ---------------------------------------------------------------------------

def test_screen_splits():
    jobs = [
        job(job_id="a", title="Java"),
        job(job_id="b", title="销售"),
        job(job_id="c", title="Golang"),
    ]
    passed, skipped = screen(jobs, rules(title_any=["Java", "Go"]))
    assert [j.job_id for j in passed] == ["a", "c"]
    assert [j.job_id for j, _ in skipped] == ["b"]


def test_screen_empty():
    assert screen([], rules()) == ([], [])
