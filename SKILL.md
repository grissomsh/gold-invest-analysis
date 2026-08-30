---
name: gold-invest-analysis
description: 黄金市场雷达 — 双分数量化监控系统。关注分(0-100, 异动驱动: 单日收益z 30% + 5日收益z 25% + 波动率突升 20% + 快讯热度 15% + 距历史新高 10%)回答"今天要不要去关注黄金市场"；温度分(0-100, 位置驱动, 四组制: 估值[金/M2、金银比、金油比、实际利率]30% + 趋势[均线偏离、动量分位]30% + 拥挤[CFTC净多、波动率]15% + 宏观[美元指数、VIX、通胀预期]25%)判断市场处于过热/偏热/中性/偏冷/过冷。定价权在伦敦/纽约，国内行为指标(换手率/国内溢价/ETF份额增速)不进分数，仅在速览作展示；央行购金(中国SAFE月度)作为慢变量区块展示。数据全自动免key：akshare(上海金/伦敦金/WTI/518880/国债/快讯三源/SAFE央行储备)+FRED(实际利率/M2/USDCNY/美元指数/VIX/美债10Y)+CFTC官方API，输出控制台表格+HTML报告+JSON+SQLite历史积累，附 --backtest 分数校准回测与 --alert 推送提醒(Server酱/PushPlus/Bark)。当用户想知道"现在黄金市场是否值得关注/要不要看一眼金价"、查询黄金市场热度/温度/拥挤度、中国央行购金/黄金储备、或讨论黄金分数模型权重阈值时使用。
---

# gold-invest-analysis — 黄金市场雷达

## 核心文件

- **`scripts/gold_analysis.py`** — 主流程与 CLI：拉数 → 主日历对齐 → 双分数 → 输出
- **`scripts/gold_config.py`** — 单点配置：数据源参数 + 双分数权重/窗口/阈值/判读分档
- **`scripts/gold_scoring.py`** — 打分纯函数层（仅标准库，可离线单测）
- **`scripts/gold_fetch.py`** — 数据获取层（全 skill 唯一 import akshare 的文件）
- **`scripts/gold_backtest.py`** — `--backtest` 校准回测（分数→未来收益, 纯标准库）
- **`scripts/gold_alert.py`** — `--alert` 阈值推送（Server酱/PushPlus/Bark）
- **`scripts/gold_data_store.py`** — SQLite 积累（ETF份额/快讯条数/价格快照/分数历史/推送去重）
- **`tests/test_gold_scoring.py`** — 打分模型离线回归（零依赖, 系统 python3 可跑）
- **`references/gold_framework.md`** — 方法论与口径坑（先读这个再动模型常量）

---

## 快速开始

在克隆的仓库目录里执行安装脚本，Skill 会装进 Claude Code 的 skills 目录：

```bash
cd gold-invest-anlaysis && bash setup.sh
```

安装完成后（位于 ~/.claude/skills/gold-invest-analysis），重启 Claude Code 即可对话式使用（"黄金现在值得看吗？""黄金市场温度多少？"）；命令行方式：

```bash
# 完整分析(约1分钟, 13个数据源); python 路径以 setup.sh 结尾打印的为准
~/.claude/skills/gold-invest-analysis/venv/bin/python \
    ~/.claude/skills/gold-invest-analysis/scripts/gold_analysis.py
```

建议盘后运行（份额数据盘后约 19:00 发布；盘中运行取前一交易日）。

### 子命令

| 功能                            | 命令                                                   |
| ------------------------------- | ------------------------------------------------------ |
| 环境自检（逐数据源探测）        | `python3 scripts/gold_analysis.py --healthcheck`       |
| 不生成 HTML（只要表格+JSON）    | `python3 scripts/gold_analysis.py --no-html`           |
| 只要 JSON                       | `python3 scripts/gold_analysis.py --json-only`         |
| 打印每因子原始值/分位/缺失原因  | `python3 scripts/gold_analysis.py --debug`             |
| 历史截面回看                    | `python3 scripts/gold_analysis.py --asof 2026-06-30`   |
| 回补ETF份额历史（首次运行建议） | `python3 gold_analysis.py --backfill-shares 60`        |
| 分数校准回测（约1分钟）         | `python3 scripts/gold_analysis.py --backtest`          |
| 运行后推送提醒（需env配key）    | `python3 scripts/gold_analysis.py --json-only --alert` |
| 查看本地数据库状态              | `python3 scripts/gold_analysis.py --stats`             |
| 打分模型离线回归                | `python3 tests/test_gold_scoring.py`                   |

---

## 输出文件

| 文件     | 位置                          | 说明                                                      |
| -------- | ----------------------------- | --------------------------------------------------------- |
| HTML报告 | `workspace/黄金市场雷达.html` | 双分数卡+快讯卡+多空速览+价格/分数/因子/溢价图+数据源状态 |
| JSON数据 | `workspace/黄金市场雷达.json` | 全部结构化结果（图表数据不含）                            |
| 回测数据 | `workspace/黄金回测.json`     | `--backtest` 分桶统计与秩IC                               |
| SQLite   | `workspace/gold_radar.db`     | ETF份额/快讯条数/价格快照/分数历史/推送去重 逐日累积      |

工作区可用环境变量 `GOLD_WORKSPACE` 覆盖。

---

## 模型一页纸

```text
关注分 = 今天要不要看(异动, 越高越该看)
    单日收益|z| 30% + 5日收益|z| 25% + 波动率突升20% + 快讯热度15% + 距历史新高10%
    (定价权在伦敦/纽约, 国内行为指标不进分数; 国内溢价仅在速览展示)
    z=squash(100×(tanh(|z|/2)+1)/2); 分位=自排除滚动分位(3年)
    ≥70 重要异动🔴 | 40-70 值得看一眼🟡 | <40 日常波动⚪

温度分 = 市场状态(位置, 越高越热: 过热=贵/拥挤/亢奋)
    估值30%: 金/M2分位12(热) + 金银比8(冷) + 金油比5(冷) + 实际利率5(冷)
    趋势30%: 250日均线偏离10 + 20/60/120日动量分位 10/6/4 (均热)
    拥挤15%: CFTC净多分位9(热) + 波动率分位6(热)
    宏观25%: 美元指数分位10(冷) + VIX分位8(热) + 通胀预期分位7(热)
    ≥80 过热🔴只减不加 | 60-80 偏热🟠持有不加仓 | 40-60 中性⚪
    | 20-40 偏冷🔵分批建仓窗口 | <20 过冷🟣左侧小步布局

缺失自动降级: 因子缺失→剔除并按剩余权重重归一, 覆盖度<50%不给分
数据时效: 陈旧源(每源限额)整体剔除; M2滞后32日/CFTC滞后4日/美元指数周频, asof不透支未发布数据
主价格链: 上海金Au99.99(¥/克) → 黄金ETF518880 → 伦敦金XAU(USD/oz)
国内溢价 = 上海金 ÷ (伦敦金/31.1035 × USDCNY) − 1, ±0.5%内为汇率基差噪声
国内背景(仅展示不进分): 申购热度=518880份额20日增速; ETF份额增速/换手率/国内溢价均因定价权原则移出分数
校准回测 --backtest: 分档分桶统计前向5/20日收益 + Spearman秩IC + 样本量门控
推送提醒 --alert: 关注升档/温度变档/大异动 ±2%, Server酱·PushPlus·Bark, 按日去重
```

温度分是**状态描述，不是买卖建议**；档位动作文案按近期方向自动修正（急跌≤−2%不谈止盈，急涨≥+2%才谈计划性减仓；伦敦金|单日|≥2%时国内溢价因子按时差失真剔除）。报告附**多空速览卡**（因子高分方向解读, 展示非预测）。全部权重、窗口、阈值、分档集中在 `scripts/gold_config.py`；方法论依据与口径坑见 `references/gold_framework.md`。

**改模型常量的纪律**：现行权重为理论结构+经验设定；`--backtest` 提供分数信息量对照基线。调整任何权重/阈值前先读 framework 文档 §5.5–§7，改动后必须重跑 `tests/test_gold_scoring.py`（127 项断言）。

---

## 数据源

| 数据                   | API                                                                               | 说明                                                       |
| ---------------------- | --------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| 上海金 Au99.99         | akshare `spot_hist_sge`                                                           | ¥/克, 约2016起, 主日历来源                                 |
| 伦敦金/伦敦银/WTI      | akshare `futures_foreign_hist('XAU'/'XAG'/'CL')`                                  | 同一外盘接口; CL 供金油比                                  |
| 黄金ETF行情            | akshare `fund_etf_hist_em('518880')`                                              | 用于展示与主链备选; 东财不可用时腾讯K线兜底                |
| ETF份额(沪)            | akshare `fund_etf_scale_sse(date)`                                                | 指定单日快照, 逐日落库累积; 仅供展示(申购热度), 不进分数    |
| ETF份额(深)            | akshare `fund_scale_daily_szse(起,止)`                                            | 备选159934, 区间直读                                       |
| 中/美国债收益率        | akshare `bond_zh_us_rate`                                                         | 速览展示                                                   |
| 实际利率/美国M2/USDCNY | FRED `fredgraph.csv`(免key)                                                       | DFII10 / M2SL / DEXCHUS                                    |
| 美元指数/VIX/美债10Y   | FRED(免key)                                                                       | DTWEXBGS(周频发布) / VIXCLS / DGS10(通胀预期=DGS10−DFII10) |
| CFTC非商业净持仓       | CFTC Socrata API `6dca-aqww`(免key)                                               | 周报, net=long−short, 全历史                               |
| 黄金相关快讯           | akshare `stock_info_global_sina` / `stock_info_global_cls` / `futures_news_shmet` | 三源合并, 免cookie; 只回最新若干条, 条数逐日积累(不可回补) |
| 央行购金(中国)         | akshare `macro_china_foreign_exchange_gold` (SAFE)                                | 月频, 滞后约2-4周; 慢变量仅展示(储备吨/近12月净增/连增月数/占外储), 不进分数 |

---

## 故障排查

```bash
python3 scripts/gold_analysis.py --healthcheck   # 逐源诊断, 失败退出码1
```

- **东财接口(ETF行情/份额)大面积 ProxyError** → 常见于开代理的环境：脚本会自动摘除代理直连重试一轮；行情仍失败则切换**腾讯K线兜底**（sources 表注明），分数不受影响——关注分不含国内行为因子，ETF 行情仅用于展示与主链备选。
- **份额增速不显示在温度因子表** → v2.1 起定价权一致性：ETF 份额增速（国内资金行为）移出分数，仅速览展示"国内申购热度"；份额仍需 `--backfill-shares 60` 回补以显示 20 日增速。
- **快讯热度分位缺失("快讯积累中")** → 快讯条数需本地逐日积累约 3–4 周（三源只回最新若干条，历史无法回补，无回补参数）；期间因子缺失并权重重归一。
- **美元指数分位多日不变** → DTWEXBGS 为周频发布（值标注周截止），一周内分位不变属正常，不是数据卡死。
- **--alert 提示"未配置推送通道"** → 需设环境变量至少其一：`GOLD_SC_SENDKEY` (Server酱) / `GOLD_PUSHPLUS_TOKEN` (PushPlus) / `GOLD_BARK_URL` (Bark)。首日运行无历史基线不触发档位规则；同一规则当日只推一次。
- **--backtest 慢** → 约 1 分钟（逐历史日重算双分数, 纯 Python），属预期；回放中快讯/份额因子缺失并重归一，覆盖度低于实盘是正常现象。
- **温度分/关注分只剩部分子分** → 对应数据源当日不可得或陈旧（见 sources 表），模型自动降级并权重归一，覆盖度 <50% 时不出分。
- **国内溢价数值偏大** → FRED DEXCHUS 为纽约午间价且滞后 5-6 个交易日，若人民币在窗口内快速波动溢价会被高估/低估；±0.5% 带内不解读。
- **M2 最新观测"落后两个月"** → 月频+约4周发布滞后所致（8月末可用7月观测），属正常，不要调小容差。
- **周一/节后运行出现"累计"涨跌** → SGE 与国际市场交易日历不同步，one_liner 会注明"距上一交易日 N 天"，为真实累计信号。
- **国际盘大跌但报告说"日+0.3%"** → 上海金 15:30 收盘，隔夜国际段次日开盘才反映；速览里"伦敦金 日涨跌"才是全时段动作。该情形下国内溢价因子会按时差失真自动剔除，动作文案也会切换为"急跌"版本（不谈止盈）。
- **关注分>70 但表格里看不到大 |z|** → 逐因子看 `--debug`，必有 |z|≥2.5 或分位极端的因子支撑；没有则属异常，请附 `--debug` 输出反馈。

更多细节: 方法论/公式/口径验证 → `references/gold_framework.md`
