"""薪资字体混淆的检测与降级。

BOSS 把薪资数字渲染成 Unicode 私有区码点，这是**有意的反爬**。
本模块不试图破解（三种方法都试过，都失败），只做检测 + 知情降级。
"""

from types import SimpleNamespace

import pytest

from src.boss.font_decode import (
    SalaryGuard,
    build_guard,
    is_obfuscated,
    pua_codepoints,
)
from src.boss.filters import evaluate
from src.boss.models import Job

# 真实抓到的混淆样本（私有区码点）
OBFUSCATED = "\ue032\ue031-\ue032\ue034K"      # 看起来像乱码的薪资
PLAIN = "20-35K"


def rules(**kw):
    base = dict(
        title_any=[], exclude_keywords=[], company_blacklist=[],
        min_salary_k=0, cities_allow=[], skip_if_applied_before=True,
        allow_unfiltered=False, on_obfuscated_salary="passthrough",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_detect_obfuscation():
    assert is_obfuscated(OBFUSCATED)
    assert not is_obfuscated(PLAIN)
    assert not is_obfuscated("")
    assert not is_obfuscated("面议")


def test_pua_codepoints():
    cps = pua_codepoints(OBFUSCATED)
    assert cps and all(c.startswith("U+E0") for c in cps)


def test_guard_trustworthy_with_plain():
    g = build_guard([PLAIN, "15-25K", "面议"])
    assert g.salary_trustworthy
    assert g.obfuscated == 0


def test_guard_detects_obfuscation():
    g = build_guard([PLAIN, OBFUSCATED])
    assert not g.salary_trustworthy
    assert g.obfuscated == 1
    assert "混淆" in g.describe()


def test_guard_all_negotiable_still_trustworthy():
    """全是'面议'不算混淆 —— 确实没有数字可解析。"""
    g = build_guard(["面议", "面议"])
    assert g.obfuscated == 0


# ---------------------------------------------------------------------------
# 降级策略
# ---------------------------------------------------------------------------

def test_passthrough_default():
    v = evaluate(Job(job_id="a", salary=OBFUSCATED),
                 rules(min_salary_k=20, on_obfuscated_salary="passthrough"))
    assert v.passed


def test_skip_mode():
    v = evaluate(Job(job_id="a", salary=OBFUSCATED),
                 rules(min_salary_k=20, on_obfuscated_salary="skip"))
    assert not v.passed
    assert "混淆" in v.reason


def test_warn_only_mode():
    v = evaluate(Job(job_id="a", salary=OBFUSCATED),
                 rules(min_salary_k=20, on_obfuscated_salary="warn_only"))
    assert v.passed


def test_plain_salary_still_filtered():
    """明文薪资必须照常被薪资下限拦截 —— 混淆检测不能误伤正常数据。"""
    v = evaluate(Job(job_id="a", salary="12-18K"),
                 rules(min_salary_k=20, on_obfuscated_salary="passthrough"))
    assert not v.passed
    assert "薪资" in v.reason


def test_no_min_salary_means_no_check():
    """没设薪资下限时，混淆与否都不影响。"""
    v = evaluate(Job(job_id="a", salary=OBFUSCATED),
                 rules(min_salary_k=0, on_obfuscated_salary="skip"))
    assert v.passed


def test_guard_empty():
    g = build_guard([])
    assert g.total == 0
    assert not g.salary_trustworthy
