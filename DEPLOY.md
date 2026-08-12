# 🚀 品品财经舆情预测系统 — 云服务器部署指南

## 为什么要迁移到云服务器？

| | 当前 Windows 本机 | 云服务器 |
|---|---|---|
| 电脑关机 | ❌ 任务停摆 | ✅ 7×24 运行 |
| 调度方式 | Windows 任务计划程序 | Linux cron |
| 开机才补跑 | ✅ 靠 FinanceCatchUp | 不需要 |
| 稳定性 | 依赖本地网络和电源 | 数据中心 SLA 99.9% |

---

## 📋 部署前检查清单

- [ ] 一台 Linux 云服务器（2核4G 起步，推荐 **阿里云轻量** 或 **腾讯云轻量**）
- [ ] 服务器已安装 Python 3.10+
- [ ] DeepSeek API Key（已有）
- [ ] 抖音/小红书 Cookie 登录态文件
- [ ] 项目代码上传到服务器

---

## 第一步：选购云服务器

### 最低配置要求

| 组件 | 为什么要 |
|------|---------|
| **CPU: 2核** | Playwright 浏览器 + Whisper 音频转写 |
| **内存: 4GB** | Whisper base 模型 ~1.4GB，加 Playwright 浏览器 |
| **硬盘: 40GB** | 日志、视频缓存、SQLite 数据库 |
| **系统: Ubuntu 22.04** | 兼容性最好 |
| **带宽: 3Mbps+** | 抖音/小红书 API 请求 |

### 各平台对比

| 平台 | 推荐型号 | 月费 | 链接 |
|------|---------|------|------|
| **阿里云轻量** | 2核4G 40GB | ~¥50/月 | swas.console.aliyun.com |
| **腾讯云轻量** | 2核4G 50GB | ~¥45/月 | cloud.tencent.com |
| **华为云 HECS** | 2核4G 40GB | ~¥55/月 | console.huaweicloud.com |

> 💡 **强烈推荐用轻量应用服务器**，比 ECS/CVM 便宜 40%，且预装了系统镜像开箱即用。

选购时选 **Ubuntu 22.04 LTS** 系统镜像。买完后你会得到一个公网 IP。

---

## 第二步：服务器初始配置

### 2.1 SSH 登录

```bash
# 在本地 Windows 上打开 PowerShell / Terminal
ssh root@你的服务器公网IP

# 首次登录会提示确认指纹，输入 yes
```

### 2.2 创建普通用户（安全起见，不要用 root 跑应用）

```bash
# 创建用户
adduser finance
# 输入密码，其他一路回车

# 授予 sudo 权限
usermod -aG sudo finance

# 切换到新用户
su - finance
```

### 2.3 更新系统 + 安装基础工具

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    git \
    curl \
    wget \
    vim \
    htop \
    build-essential \
    python3-pip \
    python3-venv \
    ffmpeg \
    libgl1-mesa-glx \
    libegl1-mesa \
    libgles2-mesa \
    libopengl0 \
    libxcb-cursor0
```

---

## 第三步：部署项目代码

### 3.1 上传代码到服务器

**方式 A：Git（推荐）**

先在 GitHub/Gitee 上建个私有仓库，然后：

```bash
# 在服务器上
cd /home/finance
git clone https://github.com/你的用户名/你的仓库.git PythonProject24
cd PythonProject24
```

**方式 B：直接 SCP 上传**

```bash
# 在本地 Windows PowerShell 中执行
scp -r D:\workspace\PythonProject24 root@服务器IP:/home/finance/
```

### 3.2 创建 Python 虚拟环境

```bash
cd /home/finance/PythonProject24
python3 -m venv .venv
source .venv/bin/activate

# 升级 pip
pip install --upgrade pip

# 安装依赖
pip install -r requirements.txt
```

### 3.3 安装 Playwright 浏览器

```bash
# 安装 Playwright 依赖
playwright install-deps

# 安装 Chromium 浏览器
playwright install chromium
```

验证安装：

```bash
python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"
```

---

## 第四步：配置适配（Windows → Linux 必须修改）

### 4.1 修改 `config.py`

需要改的地方（打开文件对照修改）：

```python
# config.py

# ==================== 路径：不用改，代码里用 os.path 是跨平台的 ✅ ====================

# ==================== 数据库：建议直接用 SQLite（免维护）====================
# MySQL_CONFIG 不用改，代码在连不上 MySQL 时会自动回退到 SQLite
# SQLite 数据库文件在 output/financial_sentiment.db

# ==================== Playwright：改成无头模式 ====================
PLAYWRIGHT_HEADLESS = True      # ★ Linux 必须用无头模式，没有显示器

# ==================== 采集窗口：可以适当放宽 ====================
POST_MAX_AGE_HOURS = 168        # 7天窗口，保持不变
POST_MAX_AGE_HOURS_LOOSE = 240  # 10天宽松窗口

# ==================== LLM 配置：保持不变 ====================
# LLM_ENABLED = True
# LLM_MODEL = "deepseek-chat"

# ==================== 视频提取：如果性能不够可以先关掉 ====================
VIDEO_EXTRACTION_ENABLED = True   # 2核4G可以开，视频不多
VIDEO_EXTRACTION_CONCURRENCY = 1  # ★ 降到1，减少资源争抢
```

> **改一个关键行：** `PLAYWRIGHT_HEADLESS = True`

### 4.2 配置 `.env` 文件

```bash
# 在项目根目录编辑 .env
cd /home/finance/PythonProject24

cat > .env << 'EOF'
DEEPSEEK_API_KEY=sk-你的DeepSeek密钥
# 可选：如果有 Vision API
VISION_OPENAI_API_KEY=sk-你的OpenAI密钥(可选)
EOF
```

### 4.3 迁移登录态 Cookie

抖音/小红书的登录态文件需要从 Windows 本机复制到服务器：

```bash
# 在本地 Windows PowerShell 中执行
# 先确认有哪些 Cookie 文件
dir D:\workspace\PythonProject24\output\*.json

# 上传到服务器
scp D:\workspace\PythonProject24\output\*_state.json root@服务器IP:/home/finance/PythonProject24/output/
scp D:\workspace\PythonProject24\output\*_pool.json root@服务器IP:/home/finance/PythonProject24/output/
```

---

## 第五步：创建 Linux 启动脚本

Windows 的 `.bat` 文件在 Linux 用不了，创建对应的 Shell 脚本：

### 5.1 主流水线脚本

```bash
cat > /home/finance/PythonProject24/run_pipeline.sh << 'EOF'
#!/bin/bash
# ===========================================================
# 每日主流水线 - cron 触发
# 采集 + 情感分析 + 涨跌预测 + 日报生成
# ===========================================================

set -e

PROJECT_DIR="/home/finance/PythonProject24"
PYTHON="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="$PROJECT_DIR/logs"
DATE_STAMP=$(date +%Y%m%d)

cd "$PROJECT_DIR"

mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/cron_pipeline_${DATE_STAMP}.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ========== 主流水线开始 ==========" >> "$LOG_FILE"

export PYTHONIOENCODING=utf-8
"$PYTHON" "$PROJECT_DIR/main.py" --once pipeline >> "$LOG_FILE" 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ 主流水线执行成功 (exit=$EXIT_CODE)" >> "$LOG_FILE"
    echo "success" > "$PROJECT_DIR/output/.pipeline_last_status.txt"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ 主流水线执行失败 (exit=$EXIT_CODE)" >> "$LOG_FILE"
    echo "failed:$EXIT_CODE" > "$PROJECT_DIR/output/.pipeline_last_status.txt"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ========== 主流水线结束 ==========" >> "$LOG_FILE"
exit $EXIT_CODE
EOF

chmod +x /home/finance/PythonProject24/run_pipeline.sh
```

### 5.2 回测脚本

```bash
cat > /home/finance/PythonProject24/run_backtest.sh << 'EOF'
#!/bin/bash
# ===========================================================
# 每日回测任务 - cron 触发
# 获取当日收盘价，比对预测准确率
# ===========================================================

set -e

PROJECT_DIR="/home/finance/PythonProject24"
PYTHON="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="$PROJECT_DIR/logs"
DATE_STAMP=$(date +%Y%m%d)

cd "$PROJECT_DIR"

mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/cron_backtest_${DATE_STAMP}.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ========== 回测任务开始 ==========" >> "$LOG_FILE"

export PYTHONIOENCODING=utf-8
"$PYTHON" "$PROJECT_DIR/main.py" --once backtest >> "$LOG_FILE" 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ 回测任务执行成功 (exit=$EXIT_CODE)" >> "$LOG_FILE"
    echo "success" > "$PROJECT_DIR/output/.backtest_last_status.txt"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ 回测任务执行失败 (exit=$EXIT_CODE)" >> "$LOG_FILE"
    echo "failed:$EXIT_CODE" > "$PROJECT_DIR/output/.backtest_last_status.txt"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ========== 回测任务结束 ==========" >> "$LOG_FILE"
exit $EXIT_CODE
EOF

chmod +x /home/finance/PythonProject24/run_backtest.sh
```

### 5.3 健康检查脚本

```bash
cat > /home/finance/PythonProject24/health_check.sh << 'EOF'
#!/bin/bash
# 健康检查 + 补跑检测

PROJECT_DIR="/home/finance/PythonProject24"
PYTHON="$PROJECT_DIR/.venv/bin/python"
export PYTHONIOENCODING=utf-8

cd "$PROJECT_DIR"

# 先输出健康状态
"$PYTHON" health_check.py

# 如果今天是交易日且 10:00 已过，检查是否需要补跑
CURRENT_HOUR=$(date +%H)
CURRENT_DAY=$(date +%u)  # 1=Mon, 5=Fri, 6=Sat, 7=Sun

if [ $CURRENT_DAY -le 5 ] && [ $CURRENT_HOUR -ge 10 ]; then
    echo "[$(date)] 交易日且已过10:00，检查是否需要补跑..." >> "$PROJECT_DIR/logs/health_check.log"
    "$PYTHON" health_check.py --catch-up >> "$PROJECT_DIR/logs/catch_up_$(date +%Y%m%d).log" 2>&1
fi
EOF

chmod +x /home/finance/PythonProject24/health_check.sh
```

---

## 第六步：配置 Cron 定时任务

```bash
# 编辑 crontab
crontab -e

# 粘贴以下内容（按 i 进入编辑模式，粘贴后按 Esc，输入 :wq 保存）
```

```cron
# ============================================================
# 品品财经舆情预测系统 - Cron 定时任务
# ============================================================

# 每日 10:00 执行主流水线（采集+分析+预测+日报）
0 10 * * * /home/finance/PythonProject24/run_pipeline.sh

# 交易日 15:35 执行回测任务（周一至周五）
35 15 * * 1-5 /home/finance/PythonProject24/run_backtest.sh

# 每 2 小时健康检查（10:00-18:00，只在交易日）
0 10,12,14,16,18 * * 1-5 /home/finance/PythonProject24/health_check.sh

# ============================================================
# 日志清理：每周日凌晨3点，删除30天前的日志
0 3 * * 0 find /home/finance/PythonProject24/logs -name "*.log" -mtime +30 -delete

# 视频缓存清理：每天凌晨2点
0 2 * * * find /home/finance/PythonProject24/output/video_cache -type f -mtime +7 -delete
```

验证 cron 配置：

```bash
# 查看已配置的定时任务
crontab -l

# 检查 cron 服务是否在运行
sudo systemctl status cron
```

---

## 第七步：测试运行

### 7.1 先手动测试一次

```bash
cd /home/finance/PythonProject24
source .venv/bin/activate

# 先测试一个小环节，确认环境正常
python -c "
from config import INDEX_CODE_MAP
from module4_index_data import IndexDataProvider
p = IndexDataProvider()
df = p.get_index_daily('000001.SH', '20260101', '20260801')
print(f'上证指数数据: {len(df)} 条')
print(df.tail(3))
"

# 测试 Playwright 是否能启动
python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://www.baidu.com')
    print(f'页面标题: {page.title()}')
    browser.close()
print('Playwright headless 模式 OK')
"

# 测试 LLM API
python -c "
from llm_sentiment import classify_single
result = classify_single('今天A股大涨，上证指数突破3300点')
print(f'LLM分类结果: {result}')
"
```

### 7.2 跑完整流水线

```bash
# 执行完整流水线（观察输出）
python main.py --once pipeline
```

观察日志：

```bash
tail -f logs/cron_pipeline_$(date +%Y%m%d).log
```

### 7.3 测试 cron 执行

```bash
# 直接用 cron 脚本跑一次
/home/finance/PythonProject24/run_pipeline.sh

# 查看结果
ls -la output/*.xlsx
cat output/.pipeline_last_status.txt
```

---

## 第八步：监控与告警

### 8.1 查看今日运行状态

```bash
cd /home/finance/PythonProject24
source .venv/bin/activate
python health_check.py --report
```

### 8.2 设置邮件通知（可选）

```bash
# 安装 mailutils
sudo apt install -y mailutils

# 配置 Postfix（选择 "Internet Site"）
sudo dpkg-reconfigure postfix
```

然后在 `run_pipeline.sh` 末尾添加：

```bash
# 失败时发邮件
if [ $EXIT_CODE -ne 0 ]; then
    echo "Pipeline failed at $(date)" | mail -s "⚠️ 财经预测流水线失败" 你的邮箱@qq.com
fi
```

### 8.3 企业微信/钉钉通知（推荐）

在 `module8_scheduler.py` 的 `MainPipeline.run()` 末尾添加 webhook 通知即可。

---

## 第九步：使用 systemd 做进程守护（高级）

如果需要更精细的控制（超时自动杀、自动重启、日志轮转）：

```bash
sudo cat > /etc/systemd/system/finance-pipeline.service << 'EOF'
[Unit]
Description=品品财经舆情预测 - 主流水线
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=finance
WorkingDirectory=/home/finance/PythonProject24
ExecStart=/home/finance/PythonProject24/.venv/bin/python main.py --once pipeline
StandardOutput=append:/home/finance/PythonProject24/logs/systemd_pipeline.log
StandardError=append:/home/finance/PythonProject24/logs/systemd_pipeline_error.log
# 超时 60 分钟
TimeoutStartSec=3600
EOF

sudo cat > /etc/systemd/system/finance-pipeline.timer << 'EOF'
[Unit]
Description=每日 10:00 触发财经预测流水线

[Timer]
OnCalendar=*-*-* 10:00:00
Persistent=true
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable finance-pipeline.timer
sudo systemctl start finance-pipeline.timer

# 查看状态
sudo systemctl status finance-pipeline.timer
sudo systemctl list-timers | grep finance
```

> 如果使用 systemd timer，可以把 crontab 里对应那行删掉，避免重复执行。

---

## 📊 成本汇总

| 项目 | 月费 |
|------|------|
| 云服务器（2核4G） | ~¥45-55 |
| DeepSeek API（~200条帖/天 × 30天） | ~¥3-5 |
| GPT-4o Vision（可选，视频分析） | ~¥2-5 |
| 流量带宽 | 包含在服务器内 |
| **合计** | **~¥50-65/月** |

> 如果关掉视频提取（`VIDEO_EXTRACTION_ENABLED = False`），降到 2核2G 的 ¥35/月也够用。

---

## 🔧 常见问题

### Q1: 抖音检测到服务器 IP 反爬怎么办？

**现象：** 搜索返回空或需要验证码

**解决：**
1. 在本机登录抖音，导出 Cookie，上传到服务器
2. 在 `config.py` 中把 `PLAYWRIGHT_HEADLESS` 设为 `False` 不行（Linux 没有显示器），但可以：
   - 用 `xvfb` 虚拟显示器：`sudo apt install xvfb && xvfb-run python main.py --once pipeline`
3. 如果持续被封，可能需要挂代理，在 `module1_account_collector.py` 的 Playwright launch 中加 `proxy` 参数

### Q2: Whisper 下载模型太慢？

```bash
# 设置镜像
export HF_ENDPOINT=https://hf-mirror.com

# 或者提前下载
python -c "import whisper; whisper.load_model('base')"
```

### Q3: 内存不够，Whisper 把服务器跑崩了？

```bash
# 降级到 tiny 模型 (~70MB)
# 编辑 config.py: WHISPER_MODEL_SIZE = "tiny"

# 或者直接关视频提取
# VIDEO_EXTRACTION_ENABLED = False
```

### Q4: 怎么把数据从 SQLite 升级到 MySQL？

项目代码已内置了 MySQL 优先 + SQLite 自动回退。只需：

```bash
# 1. 在服务器上安装 MySQL
sudo apt install mysql-server -y

# 2. 创建数据库和用户
sudo mysql -e "
CREATE DATABASE financial_sentiment CHARACTER SET utf8mb4;
CREATE USER 'finance'@'localhost' IDENTIFIED BY '你的密码';
GRANT ALL ON financial_sentiment.* TO 'finance'@'localhost';
FLUSH PRIVILEGES;
"

# 3. 执行建表 SQL
mysql -u finance -p financial_sentiment < /home/finance/PythonProject24/sql/init_db.sql

# 4. 修改 config.py 中的 MYSQL_CONFIG
# 把 password 改成你设的密码

# 5. 重启流水线，代码会自动连 MySQL
```

### Q5: 怎么从本机下载生成的 Excel 日报？

```powershell
# 在本地 Windows PowerShell 中执行
scp root@服务器IP:/home/finance/PythonProject24/output/20260806_财经舆情预测日报.xlsx ./
```

或者更好的方式——在服务器上配置一个简单的文件下载服务（如 Python HTTP Server + ngrok）。

---

## ✅ 部署完成检查清单

- [ ] SSH 能登录服务器
- [ ] `python main.py --once pipeline` 能完整跑通
- [ ] `crontab -l` 能看到定时任务
- [ ] 次日检查：日志有输出、Excel 已生成
- [ ] `health_check.py --report` 显示 🟢 正常

---

> 📝 **部署日期记录：** `____年__月__日`
>
> 🖥️ **服务器 IP：** `____.____.____.____`
>
> 🔑 **初始密码：** 已通过短信/邮件发送
