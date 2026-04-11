# 快速开始 — 从零启动 inventory_bot

本文档假设你有一台能跑 Python 3.11+ 的机器（或 Docker），一个飞书企业账号，和一张多维表格。

---

## 一、准备飞书

### 1. 创建自建应用

1. 访问 https://open.feishu.cn/app
2. 点击"**创建自建应用**"
3. 应用名称：`inventory_bot`
4. 记下 **App ID** (`cli_xxx`) 和 **App Secret**
5. 左侧菜单 → **权限管理** → 开通以下权限：
   - `bitable:app`
   - `bitable:app:readonly`
   - `im:message`（如果后续要发直接消息）
6. 右上角"**发布**" → 填版本号 → 提交审核（企业内部应用会直接通过）

### 2. 创建多维表格

1. 飞书 → **工作台** → 多维表格 → 新建
2. 按 `docs/bitable_schema.md` 中的 7 张表结构，逐一建表
3. 从浏览器地址栏复制 `base/` 和 `?table=` 之间的字符串作为 **BITABLE_APP_TOKEN**
4. 多维表格右上角"分享" → 添加协作者 → 搜索你的 `inventory_bot` 应用 → 权限 = **可编辑**

### 3. 创建群聊机器人

1. 在飞书创建一个群聊：`备货小助手`
2. 群设置 → 群机器人 → 添加机器人 → **自定义机器人**
3. 名称：`inventory_bot`
4. 安全设置勾选"**签名校验**"，记下生成的 **Secret**
5. 记下 **Webhook URL**

---

## 二、准备代码

```bash
cd /home/user/test_001/inventory_bot

# 复制 .env 模板并填入你的凭证
cp .env.example .env
vim .env    # 填 FEISHU_APP_ID / BITABLE_APP_TOKEN / FEISHU_BOT_WEBHOOK 等

# 安装依赖
pip install -r requirements.txt
```

---

## 三、先跑 Demo（离线，不需要飞书）

```bash
python -m inventory_bot.demo
```

你应该看到：

- 数据质量 0 问题
- 预测基线生成成功
- 供应商 lead time 学习
- 3 条 URGENT、2 条 OVERSTOCK、2 条死库存
- 3 张草稿 PO 总价 ¥149,200
- `✅ Demo 完成`

如果看不到 → 检查 Python 版本（需要 3.11+）和依赖安装。

---

## 四、手动触发一次真实运行

```bash
# 单次运行周报（会写入 Bitable 并推送卡片）
python -m inventory_bot.scheduler --once weekly

# 单次跑月度复盘
python -m inventory_bot.scheduler --once monthly

# 单次跑 1688 价格扫描
python -m inventory_bot.scheduler --once price
```

第一次运行前建议先用 `dry_run=True` 跑一次，可以手动 Python：

```python
from inventory_bot.workflows.weekly_alert import run_weekly_alert
report = run_weekly_alert(dry_run=True)
print(report.to_dict())
```

---

## 五、启动守护进程

```bash
python -m inventory_bot.scheduler
```

或用 systemd：

```ini
# /etc/systemd/system/inventory-bot.service
[Unit]
Description=Inventory Bot Scheduler
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/user/test_001
EnvironmentFile=/home/user/test_001/inventory_bot/.env
ExecStart=/usr/bin/python -m inventory_bot.scheduler
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now inventory-bot
sudo systemctl status inventory-bot
```

---

## 六、日常使用节奏

| 周几 | 时间 | 谁 | 做什么 |
|---|---|---|---|
| 每周一 | 08:30 | 助理 | 更新销售/库存（见 sop_assistant.md） |
| 每周一 | 09:00 | 机器人 | 自动跑 `weekly_alert`，推群卡片 |
| 每周一 | 09:30 | 老板 | 审核决策日志，填"决策"列 |
| 每周一 | 10:00 | 买手 | 下 PO，更新状态为 `ordered` |
| 周三 | 10:00 | 机器人 | 1688 价格扫描（如启用） |
| 每月 1 号 | 09:00 | 机器人 | 月度复盘卡片 + 报告 |

---

## 七、故障排查

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `FEISHU_BOT_WEBHOOK not configured — dropping card` | .env 没加载 | 确认 python-dotenv 已安装且 .env 路径对 |
| `Feishu auth failed: code=99991663` | App Secret 错 | 重新复制 |
| `Bitable table not found: 产品主数据` | 表名拼写不对 | 确认表名和 `config.BitableTables` 完全一致 |
| 签名校验失败 | Secret 没填 | 在 .env 设置 `FEISHU_BOT_SECRET` |
| 计算结果为 0 | 没有销售历史 | 先导入足够的销售数据（至少 14 天） |
