"""登录态检测与远程重登（二维码邮件）。

云服务器场景下，"二维码在哪扫"是能否无人值守的关键：
脚本在远端，人在本地。这里把二维码截成 PNG，走邮件附件送到你手上，
然后轮询等待扫码完成。
"""

from __future__ import annotations

import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

from .browser import SELECTORS, BrowserSession, detect_page_state, first_visible
from .models import CaptchaError, LoginRequiredError, SelectorsBrokenError

LOGIN_URL = "https://www.zhipin.com/web/user/?intent=0"
QR_DIR = "reports/qr"


def ensure_login(page, cfg, logger) -> None:
    """确认已登录，未登录则抛出 LoginRequiredError 交给上层处理。"""
    page.goto("https://www.zhipin.com/web/geek/jobs", wait_until="domcontentloaded")
    _random_pause(1, 2)
    state = detect_page_state(page)
    logger(f"[info] 登录态检测: {state}")
    if state == "captcha":
        raise CaptchaError("登录检查阶段出现验证码")
    if state == "login_required":
        raise LoginRequiredError("登录态失效，需要重新扫码")


def interactive_login(cfg, logger) -> None:
    """首次登录：打开浏览器让你手动扫码，回车后保存 Profile。"""
    with BrowserSession(cfg, logger) as session:
        page = session.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        logger("[info] 请在浏览器中扫码登录；登录成功后回到这里按回车。")
        input("  >>> 登录完成后按回车继续...")
        state = detect_page_state(page)
        logger(f"[info] 检测结果: {state}")
        print("[ok] Profile 已保存，后续运行会自动复用。")


def capture_filters(cfg, logger) -> str:
    """交互式录制网页端筛选条件。

    不逆向、不猜接口 —— 直接读浏览器地址栏。网址是世界最稳的 API。
    """
    with BrowserSession(cfg, logger) as session:
        page = session.new_page()
        page.goto("https://www.zhipin.com/web/geek/jobs", wait_until="domcontentloaded")
        logger("[info] 请在浏览器里点选城市 / 薪资 / 经验 / 行业等条件，然后回到这里按回车。")
        input("  >>> 筛选条件设置完成后按回车继续...")
        url = page.url
        logger(f"[info] 捕获到的搜索 URL: {url}")
        return url


def write_search_url(cfg, url: str) -> None:
    """把捕获到的 URL 写回 config.yaml 的 source.search_url。

    用文本替换而不是 yaml.dump，避免把用户的注释和格式冲掉。
    """
    cfg_file = cfg.root / "config.yaml"
    text = cfg_file.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out, in_source, done = [], False, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("source:"):
            in_source = True
        elif stripped and not stripped.startswith("#") and not line.startswith(" ") \
                and in_source:
            in_source = False
        if in_source and stripped.startswith("search_url:"):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f'{indent}search_url: "{url}"\n')
            done = True
            continue
        out.append(line)
    if not done:
        raise RuntimeError("未在 config.yaml 中找到 source.search_url，请手动填写")
    cfg_file.write_text("".join(out), encoding="utf-8")


def send_qrcode_for_relogin(page, cfg, logger) -> bool:
    """检测到登录过期后的重登流程。

    1. 跳到登录页，截取二维码
    2. 作为附件邮件发给你
    3. 轮询等待扫码完成

    Returns:
        True 表示登录成功。
    """
    logger("[warn] 登录态失效，开始远程重登流程")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    _random_pause(2, 3)

    qr_dir = cfg.root / QR_DIR
    qr_dir.mkdir(parents=True, exist_ok=True)
    qr_path = qr_dir / f"qr-{int(time.time())}.png"

    qr = first_visible(page, SELECTORS["qrcode"], timeout_ms=8000)
    if qr is None:
        raise SelectorsBrokenError("登录页找不到二维码元素，页面结构可能已变化")
    qr.screenshot(path=str(qr_path))
    logger(f"[info] 二维码已保存: {qr_path}")

    _notify_qrcode(cfg, qr_path, logger)

    deadline = time.time() + cfg.relogin.wait_timeout_sec
    logger(f"[info] 等待扫码，最长 {cfg.relogin.wait_timeout_sec} 秒...")
    while time.time() < deadline:
        _random_pause(cfg.relogin.poll_interval_sec - 1, cfg.relogin.poll_interval_sec + 1)
        state = detect_page_state(page)
        if state == "logged_in":
            logger("[ok] 扫码成功，登录已恢复")
            return True
        if state == "captcha":
            raise CaptchaError("重登过程中出现验证码")
    logger("[warn] 等待扫码超时")
    return False


def _notify_qrcode(cfg, qr_path: Path, logger) -> None:
    """把二维码送到你能看到的地方。"""
    email = cfg.report.email
    if not (cfg.relogin.enabled and email.enabled and email.to):
        logger(f"[warn] 未配置邮件通知，二维码只能手动到机器上查看: {qr_path}")
        return

    subject = "[BOSS 自动投递] 登录已失效，请扫码重登"
    body = (
        "自动投递脚本检测到 BOSS 登录态已过期。\n\n"
        "请用 BOSS App 扫描附件中的二维码完成登录。\n"
        f"扫码窗口最长 {cfg.relogin.wait_timeout_sec // 60} 分钟。\n\n"
        f"二维码原图也保存在服务器上：{qr_path}\n"
    )
    try:
        _send_email(cfg, subject, body, attachments=[qr_path])
        logger("[info] 二维码已发送到邮箱")
    except Exception as e:
        logger(f"[error] 二维码邮件发送失败: {e}")


def _send_email(cfg, subject: str, body: str, attachments: list[Path] | None = None) -> None:
    em = cfg.report.email
    if not em.smtp_host:
        raise RuntimeError("未配置 SMTP 主机")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = em.from_addr or em.username
    msg["To"] = ", ".join(em.to)
    msg.set_content(body)

    for path in attachments or []:
        data = Path(path).read_bytes()
        msg.add_attachment(
            data, maintype="image", subtype="png",
            filename=Path(path).name,
        )

    if em.use_ssl:
        server = smtplib.SMTP_SSL(em.smtp_host, em.smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(em.smtp_host, em.smtp_port, timeout=30)
        server.starttls()
    try:
        if em.username:
            server.login(em.username, em.password)
        server.send_message(msg)
    finally:
        server.quit()


def _random_pause(lo: float, hi: float) -> None:
    import random
    time.sleep(random.uniform(max(0, lo), max(lo, hi)))
