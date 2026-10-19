# 部署到阿里云 Alibaba Cloud Linux 3

## 0. 风险前置说明（务必先读）

BOSS 直聘用户协议禁止自动化操作账号。云服务器 IP 属于**数据中心 IP**，
BOSS 风控会把"异地 + 机房 IP + 自动化特征"叠加判定为高风险。
最坏结果是账号被限制沟通功能。

因此本项目的默认配置刻意保守（`dry_run: true`、`daily_cap: 24`、
投递间隔随机 20-60 秒）。**建议前 3 天保持干跑**，确认命中的职位质量 OK 后，
再逐步放开额度观察是否被限流。

如果你家里有 NAS 或常开小主机，优先用那台 —— 住宅宽带 IP 的风控信号正常得多。

## 1. 安装系统依赖

Alibaba Cloud Linux 3 / RHEL 系用 `dnf`（老版本用 `yum`）：

```bash
sudo dnf install -y python3 python3-pip xorg-x11-server-Xvfb \
    mesa-dri-drivers liberation-fonts atk at-spi2-atk cups-libs \
    libdrm libxkbcommon libXcomposite libXdamage libXrandr \
    libgbm pango alsa-lib nss

# 中文字体（截图和渲染需要，缺失会显示方块）
sudo dnf install -y wqy-microhei-fonts wqy-zenhei-fonts
```

## 2. 安装 Python 依赖

```bash
cd /opt/boss-auto-apply
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
python -m playwright install-deps chromium   # 补齐 chromium 的系统库
```

## 3. 为什么必须有 Xvfb

Chromium 在 `headless=false` 下需要一个显示服务器。服务器上没有图形桌面，
所以用 Xvfb 提供虚拟显示。

**不要用 `headless: true` 替代**：无头浏览器是 BOSS 风控的显著特征。
脚本已内置自动启动 Xvfb（检测到 `Xvfb` 命令且 `headless: false` 时），
你也可以手动常驻一个：

```bash
Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
export DISPLAY=:99
```

验证：

```bash
DISPLAY=:99 xdpyinfo | head -5     # 能输出说明 X server 正常
```

## 4. 首次登录（关键步骤）

需要图形界面交互，用 Playwright 的 Inspector 或 VNC 连过去操作。最简单的方式是
在你本地电脑先登录生成 Profile，再把 Profile 目录整体传上去：

```bash
# 本地
python -m src.boss.cli login

# 打包 Profile 上传到服务器
tar czf profile.tgz state/browser-profile
scp profile.tgz user@your-server:/opt/boss-auto-apply/
# 服务器解压到 config.yaml 里 runtime.profile_dir 指定的位置
tar xzf profile.tgz
```

> Profile 里存的是登录 Cookie，等同于账号凭据。传输务必用 scp/rsync over SSH，
> 不要走任何明文通道，也不要提交到 Git。

## 5. 配置

```bash
cp config.yaml config.yaml.bak
vi config.yaml
```

必填项：

- `source.search_url`：先在本地跑 `capture-filters` 拿到，或手动拼
- `rules.*`：至少填 `title_any` 或 `min_salary_k` 或 `cities_allow` 之一
- `report.notify.email.*`：**强烈建议配**，否则登录失效时二维码无法送达，
  无人值守就断了
- `report.notify.webhook.url`：可选，钉钉/企微/飞书机器人

敏感信息走环境变量，别写进配置文件：

```bash
export BOSS_MAIL_USER="you@example.com"
export BOSS_MAIL_PASS="授权码"
export BOSS_WEBHOOK_URL="https://oapi.dingtalk.com/robot/send?access_token=..."
```

## 6. 试运行

```bash
source .venv/bin/activate
export DISPLAY=:99

# 1) 先验证登录态还活着
python -m src.boss.cli status

# 2) 验证选择器没失效（网站没改版）
python -m src.boss.cli check-selectors

# 3) 干跑，观察筛选质量
python -m src.boss.cli run --dry-run
```

核对 `reports/report-*.html` 里的"跳过明细"：
规则是否符合预期？有没有该投的被误杀？

确认无误后，把 `config.yaml` 的 `safety.dry_run` 改成 `false`。

## 7. 定时触发

按 `schedule.windows` 配置 cron，每天三个时间窗各跑一轮：

```bash
crontab -e
```

```cron
SHELL=/bin/bash
PATH=/usr/bin:/bin
DISPLAY=:99
BOSS_MAIL_USER=you@example.com
BOSS_MAIL_PASS=你的授权码

30 9,13,20 * * * cd /opt/boss-auto-apply && .venv/bin/python -m src.boss.cli run >> logs/cron.log 2>&1
```

退出码用于告警：

| 退出码 | 含义 | 建议动作 |
| --- | --- | --- |
| 0 | 正常 | 无需处理 |
| 1 | 未预期异常 | 看 cron.log 里的 traceback |
| 2 | 登录失效 | 检查邮箱里的二维码并重扫 |
| 3 | 选择器失效 | 网站改版了，跑 `check-selectors` 定位并修 SELECTORS |
| 4 | 遇到验证码 | **不要重试**，隔天再跑，额度调低 |

## 8. 目录权限

```bash
mkdir -p state reports logs
chmod 700 state/browser-profile   # Profile 等同账号凭据
```

## 9. 日常维护

每天扫一眼日报（邮件或 webhook）就够了。需要留意的信号：

- **连续多天"采集 0 个"** → 搜索条件太窄或网站改版
- **反复出现退出码 4（验证码）** → 立即降低频率，否则可能触发账号限制
- **退出码 2 频繁** → Cookie 有效期短，检查是否在多处登录导致互踢

## 10. 安全加固建议

- Profile 目录不要放在 Web 可访问路径下
- 配置文件权限 `chmod 600 config.yaml`
- 定期备份 `state/boss.db`（投递记录），丢了会导致重复投递
- 服务器 SSH 用密钥登录，禁用密码
