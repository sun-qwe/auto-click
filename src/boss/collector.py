"""从 BOSS 搜索结果页读取职位卡片。

只读模块，风险最低。选择器全部走 SELECTORS 候选列表，
任一候选命中即可，改版后只要有一个还在就能继续工作。
"""

from __future__ import annotations

import random
import time

from .browser import SELECTORS, detect_page_state, first_visible
from .models import (
    CaptchaError,
    Job,
    LoginRequiredError,
    SelectorsBrokenError,
)


def collect(session, cfg, logger, max_pages: int | None = None) -> list[Job]:
    """翻页采集职位列表。

    Args:
        max_pages: 覆盖配置里的 source.max_pages

    Returns:
        采集到的职位列表（未去重，去重交给 state + filters）
    """
    max_pages = max_pages or cfg.source.max_pages
    page = session.new_page()
    jobs: list[Job] = []
    seen_ids: set[str] = set()

    url = cfg.source.search_url or "https://www.zhipin.com/web/geek/jobs"
    logger(f"[info] 打开搜索页: {url}")
    page.goto(url, wait_until="domcontentloaded")
    _pause(2, 3.5)

    for page_no in range(1, max_pages + 1):
        state = detect_page_state(page)
        if state == "captcha":
            raise CaptchaError(f"第 {page_no} 页出现验证码，立即停止采集")
        if state == "login_required":
            raise LoginRequiredError("采集过程中登录态失效")

        cards = page.locator(SELECTORS["job_card"][0])
        count = cards.count()
        if count == 0:
            # 换候选选择器再试一次，可能是列表容器类名变了
            for alt in SELECTORS["job_card"][1:]:
                cards = page.locator(alt)
                count = cards.count()
                if count:
                    logger(f"[warn] 主选择器失效，回退到 {alt}")
                    break
        if count == 0:
            logger(f"[warn] 第 {page_no} 页没有职位卡片，停止采集")
            break

        logger(f"[info] 第 {page_no} 页，发现 {count} 个职位")
        for i in range(count):
            try:
                job = _parse_card(cards.nth(i), page)
                if job and job.job_id not in seen_ids:
                    seen_ids.add(job.job_id)
                    jobs.append(job)
            except Exception as e:
                logger(f"[warn] 解析第 {i} 个卡片失败: {e}")
                continue

        if page_no >= max_pages:
            break
        if not _go_next_page(page, logger):
            break
        lo, hi = cfg.schedule.page_interval_sec
        _pause(lo, hi)

    page.close()
    logger(f"[info] 本轮共采集 {len(jobs)} 个职位")
    return jobs


def _parse_card(card, page) -> Job | None:
    """从一个职位卡片里提取字段。缺字段不算失败，返回部分填充的 Job。"""
    base_url = "https://www.zhipin.com"

    def text(keys, default="") -> str:
        for key in keys:
            try:
                loc = card.locator(key).first
                if loc.count() and loc.is_visible():
                    t = loc.inner_text().strip()
                    if t:
                        return t
            except Exception:
                continue
        return default

    def attr(keys, attribute, default="") -> str:
        for key in keys:
            try:
                loc = card.locator(key).first
                if loc.count():
                    v = loc.get_attribute(attribute)
                    if v:
                        return v
            except Exception:
                continue
        return default

    href = attr(SELECTORS["job_link"], "href", "")
    job_id = _extract_job_id(href) or str(abs(hash(href or text(SELECTORS["job_title"]))))
    url = href if href.startswith("http") else (base_url + href if href else "")

    # 区域常被写成 "深圳·南山区"
    area_raw = text(SELECTORS["job_area"])
    city, district = _split_area(area_raw)

    tags = ()
    for key in SELECTORS["job_tags"]:
        try:
            loc = card.locator(key)
            n = loc.count()
            if n:
                tags = tuple(
                    loc.nth(i).inner_text().strip()
                    for i in range(min(n, 8))
                    if loc.nth(i).inner_text().strip()
                )
                break
        except Exception:
            continue

    return Job(
        job_id=job_id,
        title=text(SELECTORS["job_title"]),
        company=text(SELECTORS["job_company"]),
        salary=text(SELECTORS["job_salary"]),
        city=city,
        district=district,
        tags=tags,
        url=url,
    )


def _extract_job_id(href: str) -> str:
    """从 /job_detail/abcdef123.html 里取出职位 ID。

    BOSS 的 job id 在 URL 里稳定存在，比页面上的 data- 属性更可靠。
    """
    if not href:
        return ""
    import re
    m = re.search(r"/job_detail/([A-Za-z0-9~_-]+)", href)
    return m.group(1) if m else ""


def _split_area(raw: str) -> tuple[str, str]:
    if not raw:
        return "", ""
    parts = [p.strip() for p in raw.replace("/", "·").split("·") if p.strip()]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _go_next_page(page, logger) -> bool:
    nxt = first_visible(page, SELECTORS["next_page"], timeout_ms=3000)
    if nxt is None:
        logger("[info] 没有下一页了")
        return False
    try:
        if nxt.is_disabled():
            return False
        nxt.click()
        _pause(1.5, 3)
        return True
    except Exception as e:
        logger(f"[warn] 翻页失败: {e}")
        return False


def _pause(lo: float, hi: float) -> None:
    time.sleep(random.uniform(lo, hi))
