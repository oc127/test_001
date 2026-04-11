# 智能备货预警系统 (Inventory Reorder Bot)

跨境电商多产品线智能备货决策系统。专为中国亚马逊卖家设计。

## 解决什么问题

> 多店多 SKU 卖家手里同时有几十上百个 SKU 在不同备货阶段，每个 SKU 的 lead time 不同（工艺品 60 天、3C 20 天），靠 Excel 和记忆完全管不过来。系统每周一次计算每个 SKU 的状态，过滤掉 90% 安全的，只让你拍板那 10% 真正紧急的。

## 核心能力（Phase 1 — 增强版 + 1688）

| # | 能力 | 模块 |
|---|---|---|
| 1 | **核心备货预警** —— 基于 ROP (Reorder Point) 计算每 SKU 状态 (🔴/🟡/🟢) | `core/reorder_engine.py` |
| 2 | **中国节日 lead time 智能** —— 春节/618/双11 自动延长 lead time | `core/holiday_calendar.py` |
| 3 | **销售预估 baseline** —— 自动从历史生成预估，助理只覆盖特殊日期 | `core/forecast_baseline.py` |
| 4 | **死库存反向预警** —— 检测销量持续下降 SKU，避免越备越多 | `core/dead_stock.py` |
| 5 | **数据质量校验** —— 检测异常输入，先拦后算 | `core/data_quality.py` |
| 6 | **飞书卡片决策** —— 推送可交互卡片，手机一点完成决策 | `clients/feishu.py` |
| 7 | **1688 供应商监控** —— Firecrawl 抓取价格，涨价自动告警 | `clients/supplier_1688.py` |
| 8 | **供应商 lead time 学习** —— 从历史 PO 反推真实 lead time | `core/supplier_learning.py` |
| 9 | **PO 自动生成** —— 同意下单后自动生成采购订单 | `workflows/po_generator.py` |
| 10 | **月度复盘报告** —— 统计建议命中率、决策质量 | `workflows/monthly_review.py` |

## 架构

```
                ┌──────────────────┐
                │   飞书 Bitable    │  ← 助理在这里录数据 (单一真源)
                │   (6 张表)        │  ← 采购在这里管 PO
                └────────┬─────────┘
                         │ 读/写 API
                         ▼
            ┌────────────────────────┐
            │   inventory_bot 后端   │  ← Python 服务
            │   (本项目)             │  ← 算法 + 编排 + 推送
            └────────┬───────────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   飞书机器人   1688 (Firecrawl)  ProfitLens API
   (推送+卡片)  (供应商监控)       (Phase 2 接入)
```

## 快速开始

```bash
cd inventory_bot
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入 飞书 webhook + Bitable token

# 运行端到端 demo (用 sample data)
python -m inventory_bot.demo

# 真实运行：跑一次完整周报 (需要配置 .env)
python -m inventory_bot.scheduler --once weekly

# 启动定时调度器（每周一 09:00 自动跑）
python -m inventory_bot.scheduler
```

### 文档索引

- **`docs/quickstart.md`** — 从零到跑起来的完整手把手指南
- **`docs/bitable_schema.md`** — 飞书多维表格 7 张表的字段设计
- **`docs/sop_assistant.md`** — 助理每周 SOP：如何维护数据
- **`docs/sop_buyer.md`** — 买手 SOP：从建议到下单工厂

## 项目结构

```
inventory_bot/
├── core/                    # 算法核心 (无外部依赖)
│   ├── reorder_engine.py    # 备货预警主算法
│   ├── holiday_calendar.py  # 中国节日 lead time 调整
│   ├── forecast_baseline.py # 销售预估生成
│   ├── dead_stock.py        # 死库存检测
│   ├── data_quality.py      # 数据质量校验
│   └── supplier_learning.py # 供应商 lead time 学习
├── clients/                 # 外部服务客户端
│   ├── feishu.py            # 飞书机器人 + 卡片
│   ├── bitable.py           # 飞书多维表格 API
│   ├── supplier_1688.py     # 1688 价格监控 (Firecrawl)
│   └── profitlens.py        # ProfitLens 只读 API (Phase 2)
├── workflows/               # 业务编排
│   ├── weekly_alert.py      # 每周一主流程
│   ├── event_handler.py     # 实时事件处理
│   ├── po_generator.py      # PO 自动生成
│   └── monthly_review.py    # 月度复盘
├── data_in/                 # CSV 模板 + 用户上传数据
├── data_out/                # 输出报告 + PO 草稿
├── docs/                    # 设计文档 + SOP
├── tests/                   # 测试 + sample data
├── config.py                # 配置 + 常量
├── models.py                # 数据模型 (dataclasses)
├── scheduler.py             # 定时任务入口
└── demo.py                  # 端到端演示
```

## 工作流：你团队的一周

```
周日 22:00  [助理]   登录 飞书 Bitable，粘贴本周销售 + 库存
                    ↓
                    系统自动触发数据校验
                    ↓
                    异常数据 → 飞书提醒助理修正
                    ↓
                    校验通过 → 飞书消息"已收到本周数据 ✓"

周一 06:00  [系统]   后台自动跑：
                    · 生成销售预估 baseline (90 天)
                    · 春节/旺季因子调整 lead time
                    · 计算每 SKU 的 ROP / 状态
                    · 死库存扫描
                    · 1688 供应商价格抓取

周一 09:00  [系统]   推送到飞书群:
                    · 🔴 急单卡片 × N (可点击决策)
                    · 🟡 计划单摘要
                    · 🟤 死库存提醒
                    · 💰 1688 价格变动情报

周一上午    [你]     手机点 N 下卡片 → 决策完成
                    系统自动:
                    · 创建 PO 草稿到 Bitable 采购订单表
                    · @ 采购小王
                    · 生成 PO Excel 文件

周一-周五   [采购]   跟工厂沟通 → 确认 → 打款
                    在 Bitable 更新 PO 状态: 已下单 → 生产中 → 在途 → 已入库
                    系统自动学习实际 lead time

每月 1 号   [系统]   推送月度复盘报告
                    "本月建议 X 次，采纳 Y 次，命中率 Z%"
```

## 部署

支持三种部署方式：

1. **本地 cron** —— 你自己电脑上，零成本，电脑要开机
2. **云函数** —— 阿里云 FC / 腾讯云 SCF / AWS Lambda，免费额度够用
3. **Docker** —— 自己服务器或集成进 ProfitLens

详见 `docs/DEPLOYMENT.md`

## Phase 2 路线图

跑通 Phase 1 后，集成进 ProfitLens：
- 利润数据驱动的资金最优配置
- 跨店调拨建议
- 集成进 ProfitLens Web 前端
- SaaS 对外卖
