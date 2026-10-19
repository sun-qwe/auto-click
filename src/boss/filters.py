"""职位筛选规则 —— 纯函数，无 IO，无浏览器依赖。

这是全自动模式下**唯一的风险闸门**。设计约束：

1. 纯函数：输入 (Job, Rules, set[str]) 输出 Verdict，不碰网络不碰数据库。
   "已投递过"以 frozenset 传入，让这里保持无副作用、可被单测穷举。
2. 布尔判定，不做打分。打分模型无法解释"为什么投了这一家"，
   一旦出问题你没法调。日报必须能逐条给出人话原因。
3. reject-fast：先便宜的先查（黑名单），贵的后查（薪资解析）。
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import Job, Verdict


def evaluate(job: Job, rules, applied_ids: frozenset[str] | None = None) -> Verdict:
    """判定单个职位。

    Args:
        job: 待判定职位
        rules: config.Rules
        applied_ids: 历史上已投递过的 job_id 集合

    Returns:
        Verdict。passed=False 时 reason 必填。
    """
    applied_ids = applied_ids or frozenset()

    # --- 0. 薪资被字体混淆时的知情降级 ---
    # BOSS 用私有区码点渲染薪资，数值不可信（见 font_decode.py）。
    # 这里显式处理，而不是让 parse_salary 静默失败后当"面议"放行
    # —— 那会让 min_salary_k 完全失效且毫无提示。
    if getattr(rules, "min_salary_k", 0) > 0 and job.salary:
        from .font_decode import is_obfuscated

        if is_obfuscated(job.salary):
            mode = getattr(rules, "on_obfuscated_salary", "passthrough")
            if mode == "skip":
                return Verdict.reject("薪资被字体混淆，无法核验（配置为跳过）")
            # passthrough / warn_only 都放行，由日报标注

    # --- 1. 投递过的永不重复（最便宜，也最强的安全承诺）---
    if rules.skip_if_applied_before and job.job_id in applied_ids:
        return Verdict.reject("已投递过")

    # --- 2. 公司黑名单（子串匹配）---
    hit = _first_match(rules.company_blacklist, job.company)
    if hit is not None:
        return Verdict.reject(f"公司黑名单命中「{hit}」")

    # --- 3. 排除关键词（标题 / 公司 / 薪资文本 / 标签都可能藏着坑）---
    haystack = " ".join(
        [job.title, job.company, job.salary, job.city, " ".join(job.tags)]
    )
    hit = _first_match(rules.exclude_keywords, haystack)
    if hit is not None:
        return Verdict.reject(f"排除词命中「{hit}」")

    # --- 4. 标题关键词：任一命中即可（空 = 不限制）---
    # 忽略大小写：真实职位里 "java" 和 "Java" 混用很常见，
    # 早期版本严格区分大小写导致大量 Java 岗被误判为不匹配。
    if rules.title_any and not _any_in(rules.title_any, job.title, ignore_case=True):
        return Verdict.reject(f"标题未命中关键词 {rules.title_any}")

    # --- 5. 城市白名单（空 = 不限）---
    if rules.cities_allow and job.city and not _any_in(rules.cities_allow, job.city):
        return Verdict.reject(f"城市 {job.city} 不在白名单内")

    # --- 6. 薪资下限。解析不出来（"面议"）时放行并注明 ---
    if rules.min_salary_k > 0:
        rng = job.salary_range_k()
        if rng is None:
            # 面议 > 直接丢弃：有些好职位不写薪资，交给日报让你自己看
            return Verdict.ok()
        if rng[0] < rules.min_salary_k:
            return Verdict.reject(f"薪资下限 {rng[0]}K 低于要求 {rules.min_salary_k}K")

    return Verdict.ok()


def screen(
    jobs: Iterable[Job], rules, applied_ids: frozenset[str] | None = None
) -> tuple[list[Job], list[tuple[Job, str]]]:
    """批量筛选。返回 (通过的职位, [(被跳过的职位, 原因)])。"""
    applied_ids = applied_ids or frozenset()
    passed: list[Job] = []
    skipped: list[tuple[Job, str]] = []
    for job in jobs:
        verdict = evaluate(job, rules, applied_ids)
        if verdict.passed:
            passed.append(job)
        else:
            skipped.append((job, verdict.reason))
    return passed, skipped


def _first_match(needles: Iterable[str], haystack: str,
                 ignore_case: bool = False) -> str | None:
    """返回第一个命中的词；无命中返回 None。空 needle 被忽略防止误杀全部。"""
    if not haystack:
        return None
    if ignore_case:
        haystack = haystack.lower()
    for needle in needles:
        if not needle:
            continue
        probe = needle.lower() if ignore_case else needle
        if probe in haystack:
            return needle
    return None


def _any_in(needles: Iterable[str], haystack: str,
            ignore_case: bool = False) -> bool:
    return _first_match(needles, haystack, ignore_case) is not None
