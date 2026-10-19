# BOSS 直聘自动投递工具

按你的规则每天自动筛选职位并投递，生成可审计的日报。

> **看 [VERIFICATION.md](VERIFICATION.md) 先。** 里面如实写了哪些部分真的跑过、
> 哪些只是推断。BOSS 未登录就不返回职位数据，所以**选择器必须你登录一次后校正** ——
> 这是工具自带的 `check-selectors` 命令存在的理由。

**这不是"海投神器"。** 无差别大量投递会拉低回复率，被平台判定为低质量候选，
HR 端还会看到你的历史沟通数据。所以本工具的默认值刻意保守：干跑优先、
额度可配、每个跳过都有原因。宁可少投，不要投错。

## 核心设计

| 关注点 | 做法 |
| --- | --- |
| 反风控 | 真实 Chromium + 持久化 Profile，`headless=false`，投递间隔随机 20-60 秒 |
| 幂等 | SQLite 记录，投递前先落 `attempting` 再点击 —— 崩了重跑不会重复投递 |
| 可审计 | 每个跳过都带人话原因，日报逐条列出，出问题时能定位是哪条规则错了 |
| 失败即停 | 遇验证码 / 登录失效 / 选择器失效立即中止，**从不重试** |
| 额度可控 | 分时间窗 + 每日硬上限，所有参数可调 |

## 快速开始

### 1. 安装

```bash
pip install -e ".[dev]"
python -m playwright install chromium
```

服务器部署见 [deploy/README.md](deploy/README.md)，或直接在阿里云上一键：
```bash
./deploy/setup.sh
```

### 2. 配置

```bash
cp config.example.yaml config.yaml
```

**运行前的硬性校验**：`rules` 里至少要填 `title_any`、`min_salary_k`、
`cities_allow` 之一，否则直接拒绝启动。这是防止"规则没写就开跑导致海投"的闸门。

### 3. 首次登录

```bash
python -m src.boss.cli login
```

浏览器打开后扫码，登录后按回车，会话保存在 `state/browser-profile`。

> Profile 里存的是登录 Cookie，等同账号凭据。**不要提交到 Git，不要走明文传输。**

### 4. 录制搜索条件

```bash
python -m src.boss.cli capture-filters
```

在弹出的浏览器里点选城市 / 薪资 / 经验 / 行业，回车后 URL 自动写入配置。

**为什么不直接调用接口？** BOSS 的列表接口带签名且随版本变，绕有很多失效和不稳定。
读浏览器地址栏，网址是世界最稳的 API。

### 5. 校正选择器（首次务必做）

BOSS 是 SPA，**未登录时不返回职位数据**，所以工具里有一部分选择器是从页面
JS 结构推断的，可能与实际不符。登录后跑这条命令验证：

```bash
python -m src.boss.cli check-selectors
```

- 提示「关键选择器全部正常」→ 可以直接用
- 提示「以下选择器全部失效」→ 打开 `src/boss/browser.py` 的 `SELECTORS` 表，
  照着浏览器 F12 里复制到的真实类名改正，再跑一次确认

怎么拿真实类名：浏览器打开职位列表页 → F12 → Elements → 选中职位卡片 →
右键 Copy → Copy selector，填进对应条目。

哪些标注了 `[已验证]`、哪些是 `[推测]`，见 [VERIFICATION.md](VERIFICATION.md)。

### 6. 干跑验证（千万别跳过）

```bash
python -m src.boss.cli run --dry-run
```

只采集、只判定、只出报告，**不会真的投递**。打开 `reports/report-*.html`，
重点看「跳过明细」：该投的是不是通过了？不该投的是不是被拦住了？
调整规则重复这一步，直到筛选质量满意。

想先看报告长什么样，可以生成样例：
```bash
python -m src.boss.cli demo-report
```

### 7. 正式运行

确认质量 OK 后，把 `safety.dry_run` 改成 `false`：

```bash
python -m src.boss.cli run
```

## 命令

| 命令 | 作用 |
| --- | --- |
| `login` | 首次登录，保存浏览器会话 |
| `capture-filters` | 录制网页端筛选条件，写入配置 |
| `run` | 执行一轮：采集 → 筛选 → 投递 → 日报 |
| `status` | 查看今日额度、累计统计、最近投递 |
| `check-selectors` | 校验页面选择器是否失效（网站改版预警） |
| `demo-report` | 生成一份日报样例 |

`run` 的常用参数：
```bash
python -m src.boss.cli run --dry-run      # 强制干跑，忽略配置
python -m src.boss.cli run --no-dry-run   # 强制实际投递
python -m src.boss.cli run --max-pages 1  # 只采一页，冒烟用
```

## 退出码（cron 告警依赖）

| 码 | 含义 | 该做什么 |
| --- | --- | --- |
| 0 | 正常 | 无 |
| 1 | 未预期异常 | 查日志 traceback |
| 2 | 登录失效 | 查邮箱里的二维码，扫码恢复 |
| 3 | 选择器失效 | 网站改版了，跑 `check-selectors` 定位后修 `browser.py` 的 SELECTORS |
| 4 | 遇到验证码 | **不要重试**，隔天再跑，同时调低额度 |

## 本地规则（网页端表达不了的部分）

```yaml
rules:
  title_any: ["Java", "后端", "Golang"]   # 标题命中任一（记得写别名）
  exclude_keywords: ["外包", "外派", "驻场", "劳务派遣", "培训贷"]
  company_blacklist: ["某某人力"]
  min_salary_k: 20                        # 面议的会放行，避免误杀好岗位
  cities_allow: ["深圳"]
  skip_if_applied_before: true            # 强烈建议保持 true
```

判定是**布尔 + 原因**，不做打分模型。全自动模式下，你必须能从日报反推
"为什么投了这一家"，打分说不清楚这件事。

## 项目结构

```
src/boss/            业务代码，日常只用这里
  cli.py             编排流水线（唯一出口走 _finalize）
  config.py          配置加载 + 启动前硬性校验
  filters.py         筛选纯函数（最高价值，全表驱动单测覆盖）
  state.py           SQLite 去重 / 额度 / 运行记录
  browser.py         Playwright 会话 + 选择器集中管理 + Xvfb
  auth.py            登录检测 + 二维码邮件远程重登
  collector.py       采集职位卡片（只读）
  applier.py         投递 + 节奏控制（唯一有副作用的模块）
  reporter.py        HTML 日报 + 邮件 + webhook
  font_decode.py     薪资字体混淆检测（不破解，只降级）
  models.py          数据模型与退出码
  demo.py            日报样例数据

tools/               验证与诊断，不参与日常投递（见 tools/README.md）
  check_*.py         静态检查，无需浏览器
  smoke_browser.py   假站点全链路冒烟
  dryrun_live.py     真实页面只读干跑
  verify_live.py     真实页面采集验证
  cdp/               极简 CDP 客户端

deploy/              阿里云部署手册与一键脚本
tests/               单元与端到端测试（60 项）
```

改动业务前先看 `filters.py` 和 `state.py` —— 这两块决定风险边界。
改选择器看 `browser.py` 的 `SELECTORS`（每项都标了是否为真实抓取值）。

## 测试

```bash
python -m pytest tests/ -q         # 单元 + 端到端，60 项
python tools/check_refs.py         # 跨模块引用检查
python tools/check_shell.py        # 部署脚本结构检查
python tools/check_report_html.py  # 日报内容校验
```

需要真实浏览器的只读验证脚本都在 `tools/` 下，用法见 [tools/README.md](tools/README.md)。
真实投递统一走正式 CLI，不保留硬编码的一次性投递脚本。

## 已知限制

- **薪资下限目前不可靠**。BOSS 用自定义字体加密薪资数字，三种破解方法均失败
  （见 [VERIFICATION.md](VERIFICATION.md)）。`min_salary_k` 实际可能形同虚设，
  用 `on_obfuscated_salary` 选择降级策略。
- **登录态会过期**（通常几天到几周），需要定期重新扫码。已配邮件自动送二维码，
  但仍需你手动扫一次。真正的"永久无人值守"做不到。
- **首页是滚动加载**，没有传统分页器，翻页逻辑尚未实测。
- **云服务器 IP 是风控信号**。家里有 NAS 或常开小主机的话，优先用那台。
- 网页版 BOSS 有投递额度上限，超了会静默失败。用 `status` 看今日额度。

## 风险提示

BOSS 用户协议禁止自动化操作账号，**最坏情况是账号被限制沟通功能**。
本工具在行为节奏上尽量贴近真人，但无法把风险降到零。

三条底线请务必守住：
1. 前 3 天保持干跑，确认筛选质量后再放开
2. `daily_cap` 保守设置，宁少不多
3. 遇到验证码就停，绝不重试 —— 重试是封号的主要推手
