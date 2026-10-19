"""Playwright 浏览器会话管理。

关键设计：持久化 Profile（launch_persistent_context），不是每次新建 context。
登录态保存在磁盘，重启后 Cookie 还在 —— 这决定了"扫码一次能撑多久"。

headless 默认 false：无头浏览器是 BOSS 风控的显著特征之一。
Linux 无桌面环境下配合 Xvfb 使用。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from .models import DependencyError, SelectorsBrokenError

# 页面元素选择器集中管理。BOSS 改版时只需改这里 + 跑 check-selectors。
#
# 【已用真实登录态校对】下面标 [真] 的都来自已登录状态下真实 Chrome
# 渲染的 DOM，不再是猜测：
#
#   卡片   li.job-card-box
#   标题   a.job-name
#   薪资   span.job-salary
#   公司   span.boss-name
#   地点   span.company-location
#   标签   ul.tag-list li
#   投递   a.op-btn-chat（详情页"立即沟通"）
#   已投   "沟通过" 或 "已投递" —— 两种文案都要识别
#
# BOSS 是 SPA（zhipin-geek-spa/vXXXX），职位由 JS 渲染，
# 静态 HTML 抓不到任何东西，且版本号会变。
# 稳定性策略：优先依赖 href 里的 /job_detail/ 这个结构性锚点，
# 它比 class 名稳定得多。
SELECTORS = {
    # 搜索结果页容器
    "jobs_container": [".job-card-wrap", ".card-area", ".page-jobs-main"],

    # 职位卡片 [真]
    "job_card": [
        "li.job-card-box",                  # [真]
        "li:has(a[href*='/job_detail/'])",  # 结构锚点，最稳
        ".job-card-box",
    ],
    # 卡片内字段 [真]
    "job_link": ["a[href*='/job_detail/']"],
    "job_title": ["a.job-name", ".job-name"],                    # [真]
    "job_salary": ["span.job-salary", ".job-salary"],            # [真]
    "job_company": ["span.boss-name", ".boss-name", ".company-name"],
    "job_area": ["span.company-location", ".company-location"],  # [真]
    "job_tags": ["ul.tag-list li", ".tag-list li"],              # [真]

    # 空状态 / 需要登录（未登录时真会出现）
    "empty_result": [".job-empty-wrapper", ".job-empty-box"],
    "need_login_hint": ["text=请登录查看", "text=登录后查看更多"],

    # 详情页投递按钮 [真]
    # 注意：详情页和列表页的按钮类名不同！
    #   详情页  a.btn.btn-startchat（父容器 div.detail-op > div.btns）
    #   列表页  a.op-btn-chat（推荐位）
    # 且旁边有个"感兴趣"按钮 a.btn-interest，绝不能点错。
    "apply_button": [
        "a.btn-startchat",            # [真] 详情页立即沟通
        ".btn-startchat",
        "div.detail-op a.btn-startchat",
        "a.op-btn-chat",              # 列表/推荐位
        "text=立即沟通",
    ],
    # 已投递 / 已沟通 [真：详情页侧边栏显示"沟通过"/"已投递"]
    "already_chat": [
        "text=沟通过",
        "text=已投递",
        "text=继续沟通",
        ".side-chat",
        ".side-deliver",
    ],
    # 严禁误点的按钮
    "do_not_click": [
        "a.btn-interest",             # "感兴趣" —— 不是投递
        ".btn-interest",
    ],

    # 翻页：BOSS 首页是滚动加载，没有传统分页器
    "next_page": [
        "a.ui-icon-arrow-right",
        ".pagination .next",
        "li.next a",
        "text=下一页",
    ],

    # 登录态指示器 [已验证: login-btn / header-login-btn]
    "logged_in_marker": [
        ".nav-figure img",
        "a[href*='/geek/mobile']",
        ".user-nav",
        ".header-resume",
    ],
    "login_marker": [
        "text=请先登录",
        ".login-btn",           # [已验证]
        ".header-login-btn",    # [已验证]
        "text=登录/注册",
    ],
    "captcha_marker": [
        ".nc_wrapper",
        "#nc_1_wrapper",
        ".yidun_bgimg",
        "text=请完成安全验证",
        "text=安全验证",
        ".verify-wrap",
    ],
    "qrcode": [
        ".qrcode img",
        "canvas.qrcode",
        ".login-qrcode img",
    ],
}


def first_visible(page, keys: list[str], timeout_ms: int = 3000):
    """依次尝试候选选择器，返回第一个可见元素的 locator。

    全部失败返回 None —— 调用方负责判断这是"页面没加载完"还是"改版了"。
    """
    for key in keys:
        try:
            loc = page.locator(key).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            if loc.is_visible():
                return loc
        except Exception:
            continue
    return None


class BrowserSession:
    """管理 Playwright 浏览器生命周期。"""

    def __init__(self, cfg, logger=None):
        self.cfg = cfg
        self.logger = logger or _null_logger
        self._playwright = None
        self._context = None
        self._xvfb_proc = None

    # -----------------------------------------------------------------
    def __enter__(self) -> "BrowserSession":
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            if exc.name == "playwright":
                raise DependencyError(
                    "缺少 playwright 库。请先安装：\n"
                    "    pip install playwright\n"
                    "    python -m playwright install chromium\n"
                    "（Linux 服务器还需要 Xvfb，见 deploy/README.md）"
                ) from exc
            raise

        self._playwright = sync_playwright().start()

        profile = Path(self.cfg.path(self.cfg.runtime.profile_dir))
        profile.mkdir(parents=True, exist_ok=True)

        self._start_xvfb_if_needed()

        launch_kwargs = dict(
            headless=self.cfg.runtime.headless,
            viewport={"width": self.cfg.runtime.viewport_width,
                      "height": self.cfg.runtime.viewport_height},
            locale=self.cfg.runtime.locale,
            timezone_id=self.cfg.runtime.timezone,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            slow_mo=self.cfg.runtime.slow_mo_ms,
        )

        self._context = self._playwright.chromium.launch_persistent_context(
            str(profile), **launch_kwargs
        )
        self._strip_webdriver_flag()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._stop_xvfb()

    # -----------------------------------------------------------------
    @property
    def context(self):
        if self._context is None:
            raise RuntimeError("BrowserSession 未启动，请用在 with 语句中")
        return self._context

    def new_page(self):
        page = self.context.new_page()
        page.set_default_timeout(self.cfg.runtime.page_timeout_ms)
        return page

    # -----------------------------------------------------------------
    def _strip_webdriver_flag(self) -> None:
        """抹掉 navigator.webdriver —— 最经典的无头检测点。"""
        script = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh']);
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        window.chrome = { runtime: {} };
        """
        try:
            self._context.add_init_script(script)
        except Exception as e:  # 失败不该阻断主流程，但要知道
            self.logger(f"[warn] 注入反检测脚本失败: {e}")

    # -----------------------------------------------------------------
    # Xvfb：Linux 无桌面时给 Chromium 一个虚拟显示
    # -----------------------------------------------------------------
    def _start_xvfb_if_needed(self) -> None:
        if sys.platform.startswith("win"):
            return
        if self.cfg.runtime.headless:
            return          # 真无头就不需要
        if not shutil.which("Xvfb"):
            self.logger("[warn] 未检测到 Xvfb，headless=false 可能无法启动。"
                        " 在服务器上请安装 xvfb，或改用 Xvfb-run。")
            return
        display = self.cfg.runtime.xvfb_display
        try:
            proc = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1440x900x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._xvfb_proc = proc
            import os
            os.environ["DISPLAY"] = display
            time.sleep(1.0)     # 等 X server 就绪
            self.logger(f"[info] 已启动 Xvfb {display}")
        except Exception as e:
            self.logger(f"[warn] Xvfb 启动失败: {e}")

    def _stop_xvfb(self) -> None:
        if self._xvfb_proc and self._xvfb_proc.poll() is None:
            try:
                self._xvfb_proc.terminate()
                self._xvfb_proc.wait(timeout=5)
            except Exception:
                try:
                    self._xvfb_proc.kill()
                except Exception:
                    pass
        self._xvfb_proc = None


def detect_page_state(page) -> str:
    """判定当前页面状态：logged_in / login_required / captcha / unknown。

    顺序很重要：验证码优先判定。风控页面上登录态元素往往还在，
    如果先查登录态，会把验证码页误判成"已登录"然后继续操作 —— 那是加速封号的行为。
    """
    if first_visible(page, SELECTORS["captcha_marker"], timeout_ms=1500):
        return "captcha"
    if first_visible(page, SELECTORS["logged_in_marker"], timeout_ms=1500):
        return "logged_in"
    if first_visible(page, SELECTORS["login_marker"], timeout_ms=1500):
        return "login_required"
    return "unknown"


def _null_logger(msg: str) -> None:
    print(msg)
