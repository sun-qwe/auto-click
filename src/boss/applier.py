"""执行投递动作 + 节奏控制。

这是唯一会对外产生副作用的模块，所以规则最严：

1. 每次投递前必须先把 attempting 落库，再点按钮（先写后做，保证幂等）。
2. 遇到验证码立即抛出并停止，**绝不重试** —— 重试是风控升级的主要推手。
3. 连续失败达到阈值就停，不在异常状态下持续试探。
"""

from __future__ import annotations

import random
import time

from .browser import SELECTORS, detect_page_state, first_visible
from .models import (
    ApplyRecord,
    CaptchaError,
    Job,
    LoginRequiredError,
    RunStats,
    SelectorsBrokenError,
)


def apply_to_jobs(session, cfg, state, jobs: list[Job], logger) -> RunStats:
    """按额度与节奏投递一批职位。

    Returns:
        RunStats，含成功 / 失败 / 跳过计数与停止原因。
    """
    stats = RunStats(dry_run=cfg.safety.dry_run)
    page = session.new_page()

    remaining = state.remaining_today(cfg.schedule.daily_cap)
    cap = min(cfg.schedule.per_window_cap, remaining)
    logger(f"[info] 本轮额度: {cap}（今日剩余 {remaining}）")

    if cap <= 0:
        stats.stopped_reason = "今日额度已用尽"
        page.close()
        return stats

    consecutive_failures = 0
    for job in jobs:
        if stats.applied >= cap:
            stats.stopped_reason = "达到本时间窗上限"
            break
        if state.remaining_today(cfg.schedule.daily_cap) <= 0:
            stats.stopped_reason = "今日额度已用尽"
            break

        try:
            ok, msg = _apply_one(page, cfg, state, job, logger)
        except CaptchaError:
            # 不重试、不 catch、不降级 —— 直接透传，让 pipeline 停
            stats.stopped_reason = "遇到验证码，已停止"
            page.close()
            raise
        except LoginRequiredError:
            # 登录已挂时必须立刻停。继续打开页面只会放大风险信号，
            # 而且每个后续职位都会重复失败同一原因。
            stats.stopped_reason = "登录态失效，已停止"
            page.close()
            raise
        except SelectorsBrokenError:
            stats.stopped_reason = "页面结构异常，已停止"
            page.close()
            raise
        except Exception as e:
            consecutive_failures += 1
            stats.failed += 1
            logger(f"[error] 投递 {job.title} 失败: {e}")
            state.mark_failed(job, str(e))
            if consecutive_failures >= cfg.safety.max_consecutive_failures:
                stats.stopped_reason = f"连续失败 {consecutive_failures} 次，已停止"
                break
            continue

        consecutive_failures = 0
        if ok:
            stats.applied += 1
        else:
            stats.skipped += 1
            logger(f"[info] 跳过 {job.title}: {msg}")

        _random_pause(*cfg.schedule.min_interval_sec)

    page.close()
    return stats


def _apply_one(page, cfg, state, job: Job, logger) -> tuple[bool, str]:
    """投递单个职位。返回 (是否成功投递, 说明)。"""
    if not job.url:
        state.record_skip(job, "缺少职位链接")
        return False, "缺少职位链接"

    page.goto(job.url, wait_until="domcontentloaded")
    _random_pause(1.5, 3)

    page_state = detect_page_state(page)
    if page_state == "captcha":
        raise CaptchaError(f"打开 {job.title} 时出现验证码")
    if page_state == "login_required":
        raise LoginRequiredError("投递过程中登录态失效")

    # 已沟通过的不再点 —— 网页端会显示"继续沟通"
    already = first_visible(page, SELECTORS["already_chat"], timeout_ms=1500)
    if already is not None:
        state.mark_applied(job)     # 认定此前已投递，避免以后重复
        return False, "此前已沟通过"

    btn = first_visible(page, SELECTORS["apply_button"], timeout_ms=5000)
    if btn is None:
        # 找不到按钮大概率是改版，不是偶发抖动 —— 按 SelectorsBroken 处理，
        # 避免在一堆职位上反复重试同一个失效的选择器
        raise SelectorsBrokenError(f"找不到投递按钮: {job.title}")

    if cfg.safety.dry_run:
        logger(f"[dry-run] 将会投递: {job.title} @ {job.company}")
        state.record_seen(job)
        # 干跑不计入额度，也不写 applied —— 不代表真的投出去了
        return True, "干跑（未实际投递）"

    # 先标记 attempting，再点击。崩在中间也不会重复投递。
    state.mark_attempting(job)
    try:
        btn.click()
        _random_pause(2, 3.5)
    except Exception as e:
        state.mark_failed(job, f"点击失败: {e}")
        raise

    # 校验是否真的投出去了
    if _confirm_sent(page):
        state.mark_applied(job)
        logger(f"[ok] 已投递: {job.title} @ {job.company}")
        return True, "已投递"
    state.mark_failed(job, "点击后未检测到成功状态")
    return False, "点击后未确认成功"


def _confirm_sent(page) -> bool:
    """判断是否投递成功。

    成功信号：出现"继续沟通"/"已沟通"，或出现聊天输入框；
    失败信号：出现验证码。宁可判 false（记 failed，下轮可重试），
    也不要误判成功 —— 误判会导致重复触达。
    """
    if first_visible(page, SELECTORS["captcha_marker"], timeout_ms=1000):
        raise CaptchaError("投递后出现验证码")
    if first_visible(page, SELECTORS["already_chat"], timeout_ms=2000):
        return True
    if first_visible(page, [".chat-input", "textarea[placeholder*='聊']"], timeout_ms=2000):
        return True
    return False


def _random_pause(lo: float, hi: float) -> None:
    time.sleep(random.uniform(lo, hi))
