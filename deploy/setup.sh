#!/usr/bin/env bash
# 阿里云 Alibaba Cloud Linux 3 一键部署 / 更新
#
#   chmod +x deploy/setup.sh && ./deploy/setup.sh
#
# 幂等：重复执行安全。安装失败会在对应步骤报错退出。

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/boss-auto-apply}"
VENV="$APP_DIR/.venv"
DISPLAY_NUM="${DISPLAY_NUM:-:99}"
PIP_MIRROR="${PIP_MIRROR:-https://mirrors.aliyun.com/pypi/simple/}"

RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GRN}[setup]${NC} $*"; }
warn() { echo -e "${YEL}[warn]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

command -v dnf >/dev/null 2>&1 && PKG=dnf || PKG=yum

# ---------------------------------------------------------------------------
log "1/6 安装系统依赖"
# ---------------------------------------------------------------------------
sudo $PKG install -y python3 python3-devel python3-pip git \
    xorg-x11-server-Xvfb mesa-dri-drivers libdrm libgbm \
    atk at-spi2-atk cups-libs libxkbcommon libXcomposite \
    libXdamage libXrandr pango alsa-lib nss 2>&1 | tail -5

log "安装中文字体（缺失会导致截图是方块）"
sudo $PKG install -y wqy-microhei-fonts wqy-zenhei-fonts 2>&1 | tail -3 \
    || warn "字体包不可用，可稍后手动安装"

# ---------------------------------------------------------------------------
log "2/6 安装 Xvfb 到 systemd（开机自启，比放进 cron 更稳）"
# ---------------------------------------------------------------------------
sudo tee /etc/systemd/system/xvfb.service >/dev/null <<EOF
[Unit]
Description=Xvfb virtual display for BOSS auto-apply
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb ${DISPLAY_NUM} -screen 0 1440x900x24 -nolisten tcp
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now xvfb.service
sleep 1
if systemctl is-active --quiet xvfb.service; then
    log "Xvfb 已启动"
else
    err "Xvfb 启动失败，浏览器将无法运行。执行 journalctl -u xvfb 查看原因"
    exit 1
fi

# ---------------------------------------------------------------------------
log "3/6 准备应用目录 $APP_DIR"
# ---------------------------------------------------------------------------
if [ ! -d "$APP_DIR" ]; then
    sudo mkdir -p "$APP_DIR"
    sudo chown -R "$(whoami)": "$APP_DIR"
fi
cd "$APP_DIR"

# 代码不在本机时提示（本地已开发好的情况跳过）
if [ ! -f "$APP_DIR/pyproject.toml" ]; then
    warn "目录下没有 pyproject.toml。"
    warn "请把项目代码放到 $APP_DIR 后重新执行本脚本："
    warn "  rsync -av --exclude=state --exclude=reports ./ user@server:$APP_DIR/"
    exit 1
fi

mkdir -p state reports logs

# ---------------------------------------------------------------------------
log "4/6 创建虚拟环境并安装 Python 依赖（国内镜像）"
# ---------------------------------------------------------------------------
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip -i "$PIP_MIRROR" 2>&1 | tail -2
pip install -e . -i "$PIP_MIRROR" 2>&1 | tail -5

log "安装 Chromium（约 150MB，请耐心）"
# Playwright 官方 CDN 慢时可改为国内镜像
if [ -n "${PLAYWRIGHT_DOWNLOAD_HOST:-}" ]; then
    log "使用镜像源 $PLAYWRIGHT_DOWNLOAD_HOST"
fi
python -m playwright install chromium 2>&1 | tail -5
sudo "$VENV/bin/python" -m playwright install-deps chromium 2>&1 | tail -3 \
    || warn "install-deps 部分失败，若运行报缺库请手动补"

# ---------------------------------------------------------------------------
log "5/6 配置文件"
# ---------------------------------------------------------------------------
if [ ! -f "$APP_DIR/config.yaml" ]; then
    cp "$APP_DIR/config.example.yaml" "$APP_DIR/config.yaml"
    chmod 600 "$APP_DIR/config.yaml"
    warn "已生成 config.yaml，请务必填写后再运行："
    warn "  vi $APP_DIR/config.yaml"
else
    log "config.yaml 已存在，跳过"
fi

chmod 700 "$APP_DIR/state" 2>/dev/null || true

# ---------------------------------------------------------------------------
log "6/6 自检"
# ---------------------------------------------------------------------------
"$VENV/bin/python" -m src.boss.cli --help >/dev/null 2>&1 \
    && log "CLI 可正常调用" \
    || { err "CLI 调用失败，请检查依赖安装"; exit 1; }

cat <<EOF

${GRN}部署完成${NC}

接下来三步：
  1. 填配置：      vi $APP_DIR/config.yaml
                   （必填 source.search_url 和 rules.*）
  2. 传 Profile：  本地执行 login 后把 state/browser-profile 传上来
  3. 干跑验证：    cd $APP_DIR && DISPLAY=$DISPLAY_NUM .venv/bin/python -m src.boss.cli run --dry-run

配好 cron（参考 deploy/README.md 第 7 节）后即为无人值守。
退出码 2 = 需要扫码，届时留意邮件里的二维码。
EOF
