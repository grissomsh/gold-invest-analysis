# 黄金市场雷达 (gold-invest-analysis)

一个**确定性的黄金市场状态监控系统**：每天自动拉取 13 个免 key 数据源，输出两个 0–100 的分数，回答两个问题——

|      | 关注分 (Attention)               | 温度分 (Temperature)                   |
| ---- | -------------------------------- | -------------------------------------- |
| 回答 | 今天要不要去关注黄金市场         | 市场现在处于什么状态                   |
| 构成 | **异动**（相对自身历史的罕见度） | **位置**（当前水平在自身历史中的分位） |
| 变化 | 快（一天内可跳变）               | 慢（跟随估值/拥挤度漂移）              |

判读：关注分 ≥70 重要异动🔴 / 40–70 值得看一眼🟡 / <40 日常波动⚪；温度分 ≥80 过热🔴（只减不加）/ 60–80 偏热🟠 / 40–60 中性⚪ / 20–40 偏冷🔵 / <20 过冷🟣（左侧布局）。**分数是状态描述，不构成投资建议。**

## 因子体系

- **关注分（5 因子）**：单日收益 |z| 30% · 5日收益 |z| 25% · 波动率突升 20% · 快讯热度 15% · 距历史新高 10%
- **温度分（13 因子，四组）**：估值 30%（金/M2、金银比、金油比、实际利率）· 趋势 30%（250日均线偏离、20/60/120日动量）· 拥挤 20%（CFTC 净多、ETF 份额增速、波动率）· 宏观 20%（美元指数、VIX、通胀预期）

设计原则：金价定价权在伦敦/纽约，国内行为指标不进分数；全部因子走统一范式（自排除滚动分位 / z+squash / 分段线性映射），任一数据源失败自动降级并权重重归一，覆盖度如实披露；打分层纯标准库、可离线单测。

## 数据源（全部免 key、无 cookie）

akshare：上海金 Au99.99、伦敦金/银、WTI 原油、黄金 ETF 518880、ETF 份额（沪深）、国债收益率、快讯三源（新浪全球快讯/财联社/上海金属网）｜FRED：实际利率 DFII10、美国 M2、USDCNY、美元指数 DTWEXBGS、VIX、美债 10Y｜CFTC Socrata API：非商业净持仓周报。

## 快速开始

```bash
git clone git@github.com:grissomsh/gold-invest-analysis.git
cd gold-invest-analysis && bash setup.sh
```

安装脚本会创建 venv 并把 skill 装到 `~/.claude/skills/gold-invest-analysis/`，重启 Claude Code 即可对话式使用（"黄金现在值得看吗？"）。

```bash
# 完整分析(约1分钟, 13个数据源)
~/.claude/skills/gold-invest-analysis/venv/bin/python \
    ~/.claude/skills/gold-invest-analysis/scripts/gold_analysis.py
```

### 子命令

| 功能                                           | 命令                                                    |
| ---------------------------------------------- | ------------------------------------------------------- |
| 环境自检（逐数据源探测）                       | `python3 scripts/gold_analysis.py --healthcheck`        |
| 历史截面回看                                   | `python3 scripts/gold_analysis.py --asof 2026-06-30`    |
| 分数校准回测（约1分钟）                        | `python3 scripts/gold_analysis.py --backtest`           |
| 推送提醒（Server酱/PushPlus/Bark，env 配 key） | `python3 scripts/gold_analysis.py --json-only --alert`  |
| 回补 ETF 份额历史（首次运行建议）              | `python3 scripts/gold_analysis.py --backfill-shares 60` |
| 打分模型离线回归（零依赖）                     | `python3 tests/test_gold_scoring.py`                    |

## 输出

控制台双分数表 + 一句话结论 + 快讯列表 + 多空速览，`workspace/` 下生成 HTML 报告（ECharts 图表：价格 rebase、分数历史、因子贡献、国内溢价）、JSON、SQLite 逐日积累（份额/快讯条数/分数历史/推送去重）。`--backtest` 另产出分档前向收益统计与 Spearman 秩 IC（首个基线：温度分 20 日 IC = −0.137，温度越高未来收益越低，与"过热只减不加"语义一致）。

## 文档

- [SKILL.md](SKILL.md) — 使用说明 / 模型一页纸 / 数据源 / 故障排查
- [references/gold_framework.md](references/gold_framework.md) — 方法论、因子口径、回测基线、口径坑与 v2 借鉴别鉴

## 免责声明

本项目输出为公开数据的自动化状态描述，仅供研究参考，不构成任何投资建议。权重为理论结构+经验设定，未经统计拟合校准。
