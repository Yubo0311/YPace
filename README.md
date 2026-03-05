# YPace

自动抢订脚本，场地预约系统。

支持定时登录、准时抢订、点选验证码自动识别、自动支付。

---

## 功能

- 提前指定分钟数登录，到点准时访问预约页
- 自动识别并点击空闲时间段（绿色方块），同一场地最多选 2 个时段
- 自动勾选「已阅读并同意预约须知」并提交
- 自动处理点选汉字验证码（接入[超级鹰](https://www.chaojiying.com/) API），无超级鹰账号时等待手动点击
- 自动点击支付完成订单
- 支持配置多个场地（按顺序尝试）

---

## 环境要求

- Python 3.10+
- [uv](https://github.com/astral-sh/uv)（推荐）或 pip

---

## 安装

```bash
# 安装依赖
uv sync
# 或
pip install -r requirements.txt

# 安装 Playwright 浏览器
uv run playwright install chromium
# 或
playwright install chromium
```

---

## 配置

### 1. 填写账号信息

```bash
cp credentials.env.example credentials.env
```

编辑 `credentials.env`：

```env
PKU_USERNAME=你的学号
PKU_PASSWORD=你的密码

# 超级鹰（可选，用于自动识别点选验证码）
# 注册：https://www.chaojiying.com/
# softid 在「软件管理」中创建软件后获取
CHAOJIYING_USERNAME=
CHAOJIYING_PASSWORD=
CHAOJIYING_SOFTID=
```

> `credentials.env` 已在 `.gitignore` 中，不会被提交。

### 2. 编辑预约配置

编辑 `config.yaml`：

```yaml
venues:
  - name: 五四体育中心-室外排球场
    enabled: true
    venue_id: 84               # 场地预约页面 URL 中的数字
    priority_slots:            # 优先选的时段，按顺序尝试
      - "18:00-19:00"
      - "19:00-20:00"
    book_days_ahead: [0]       # 0=今天, 1=明天（可多个）

  - name: 邱德拔体育馆羽毛球
    enabled: false
    venue_id: 12               # 替换为实际 ID
    priority_slots:
      - "19:00-20:00"
    book_days_ahead: [1]

booking_open_time: "10:00"     # 预约每天几点开放
pre_login_minutes: 3           # 提前几分钟登录
headless: false                # false = 显示浏览器窗口
```

**如何找到 `venue_id`：** 手动进入目标场地的预约页面，URL 最后的数字即为 `venue_id`，例如：
```
https://epe.pku.edu.cn/venue/venue-reservation/84
                                                ^^
                                            venue_id = 84
```

---

## 运行

```bash
uv run python main.py
```

程序会自动：

1. 等待至 `booking_open_time - pre_login_minutes`（例如 09:57）
2. 启动浏览器并通过 IAAA 统一身份认证登录
3. 等待至 `booking_open_time`（例如 10:00:00）
4. 直接访问场地预约 URL
5. 选择日期 → 点击优先时段 → 勾选协议 → 提交
6. 自动处理验证码 → 支付

---

## 验证码说明

本系统使用**点选汉字验证码**（提交时弹出）。

| 情况 | 行为 |
|------|------|
| 已配置超级鹰 | 自动截图 → API 识别 → 自动点击 |
| 未配置超级鹰 | 弹窗出现后等待你在浏览器窗口手动点击（最多等 60 秒） |

推荐配置超级鹰以实现全自动，单次识别费用极低（约 0.01 元）。

---

## 文件结构

```
YPace/
├── main.py                  # 入口：定时等待、登录、调度
├── config.yaml              # 场地、时段、定时配置
├── credentials.env          # 账号密码（gitignored，需自建）
├── credentials.env.example  # 账号模板
├── requirements.txt
└── src/
    ├── auth.py              # IAAA 登录流程
    ├── booker.py            # 选日期、选时段、提交、支付
    ├── captcha.py           # 文字验证码 + 点选验证码处理
    └── config_loader.py     # 读取 .env 和 config.yaml
```

---

## 注意事项

- 脚本仅供个人便利使用，请勿滥用或高频抢占资源
- 预约规则：同一场地同一订单最多选 2 个时段，不同场地不可同时预约
- 遇到问题可查看 `logs/booking.log` 或 `screenshots/` 目录下的截图
