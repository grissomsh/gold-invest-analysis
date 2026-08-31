# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目是什么

黄金市场雷达（gold-invest-analysis）— 双分数黄金状态监控系统：关注分（异动，今天要不要看）+ 温度分（位置，市场多热），全自动免 key 数据源（akshare + FRED + CFTC + 快讯三源 + SAFE 央行储备 + WGC 文件导入），输出控制台 / HTML / JSON / SQLite。部署形态是 Claude Code skill（SKILL.md 为安装后文档），本仓库是源码。

## 常用命令

```bash
python3 tests/test_gold_scoring.py     # 打分回归测试(133项断言, 纯标准库零依赖, 系统python3可直接跑) — 改任何权重/阈值/打分逻辑后必跑
bash setup.sh                          # 重装到 ~/.claude/skills/gold-invest-analysis + venv
~/.etf-skill/venv/bin/python scripts/gold_analysis.py                     # 全量运行(约1分钟, 14源)
~/.etf-skill/venv/bin/python scripts/gold_analysis.py --healthcheck       # 逐数据源诊断
~/.etf-skill/venv/bin/python scripts/gold_analysis.py --json-only --alert # cron场景: 只写JSON+推送
~/.etf-skill/venv/bin/python scripts/gold_analysis.py --backtest          # 分数校准回测(约1分钟)
~/.etf-skill/venv/bin/python scripts/gold_analysis.py --asof 2026-06-30   # 历史截面回看
```

注意：akshare/requests 只装在 `~/.etf-skill/venv`（系统 python3 无 akshare，但测试不需要它）；打分层 gold_scoring.py 与 backtest/cbfile 均纯标准库，可用任意 python3 跑。东财接口在本机代理下间歇性全挂是环境问题，脚本有三层兜底（摘代理重试→腾讯K线→因子降级），分数不受影响，不要当 bug 修。

## 架构

**数据流**：`gold_fetch.py`（唯一 import akshare/requests 的文件）→ `gold_analysis.py`（拉取编排 + 主日历对齐 + 组装报告）→ `gold_scoring.py`（纯函数打分）→ `gold_report.py`（控制台/HTML/JSON）+ `gold_data_store.py`（SQLite 逐日积累）。三个独立子模块：`gold_backtest.py`（分数→未来收益回放）、`gold_alert.py`（推送规则，Server酱/PushPlus/Bark，SQLite 按日去重）、`gold_cbfile.py`（WGC Goldhub 手动导入的 xlsx 解析，纯标准库）。

**核心范式（不可违背）**：所有因子走三种归一之一——`rolling_percentile`（自排除滚动分位，位置类）、`zscore_tail+squash`（异动类）、`linear_map`（分段线性插值+钳制，阈值类）；因子缺失→剔除并按剩余权重重归一（`aggregate`），覆盖度<0.5 不出分，陈旧源在 `_gate` 整体剔除。主日历=上海金 SGE 日期，其余源前向填充（`align_series`，每源 ALIGN_TOL 容差，月频 M2 等有 PUBLISH_LAGS 防 asof 前视）。

**语义决策（改代码别推翻，详见 references/gold_framework.md）**：双分数严格分离（关注=异动、温度=位置）；温度分越高越热，估值贵/拥挤/动量都是加分，只有金银比/金油比/实际利率/美元指数是"大→冷"反向项；**定价权原则**——金价由伦敦/纽约定价，国内行为指标（ETF换手率/国内溢价/ETF份额增速）一律不进分数，只作速览展示；央行购金（SAFE+WGC）同为慢变量展示区块不进分。动作文案是方向感知的：急跌(≤−2%)不追卖不抄底、减仓判据="反弹能否收复急跌失地"、绝不用"跌破前低"当触发、急涨才谈止盈。

**数据源坑速查**：DTWEXBGS 周频发布（容差须跨周）；FRED DCOILBRENTOU 有反爬（用 akshare CL 代替）；`index_news_sentiment_scope` 源站死亡；IMF 公开数据 API 是死路（元数据可读、data 空包——记录在 framework §6.12，别重试）；快讯三源只回最新若干条不可回补（news_log 逐日积累，0 是真实观测、三源全挂不落库）；`fetch_news` 返回 `(rows, 成功源数)` 元组，改回裸 list 会炸 `update_news`；WGC 文件土耳其有两行（保留 'Turkey'、剔除 'Türkiye, Republic of'）防全球合计重复；`macro_cons_gold` 是 COMEX 库存不是央行数据。

## 改常量的纪律

全部权重/窗口/阈值/分档集中在 `scripts/gold_config.py`（含注释依据）；改前先读 `references/gold_framework.md` 对应小节（窗口/容差/滞后限额必须联动同数量级）；改完必跑 `python3 tests/test_gold_scoring.py`（含配置自检与金样场景回归）；权重是理论设定未经统计拟合，`--backtest` 首轮基线（温度分20日秩IC=-0.137）在 framework §5.5 作为对照。

## 输出与数据

运行产物全部在 `workspace/`（已 gitignore）：`黄金市场雷达.html`、`黄金市场雷达.json`、`黄金回测.json`、`gold_radar.db`（etf_shares/news_log/price_snapshot/score_history/alert_log）；WGC 手动导入文件放 `workspace/data/`。环境变量 `GOLD_WORKSPACE` 可覆盖。HTML 模板在 gold_report.py 内（@TOKEN@ 替换 + ECharts 三 CDN 兜底）；改 HTML 后建议把生成文件的内联脚本过 `node --check`（历史教训：Python 格式语法误写入 JS 模板字符串）。

## 用户偏好（务必遵守）

MD 文档不硬换行（段落单物理长行，表格/代码块除外）；git commit 不加 Co-Authored-By 尾注；小改动本地累积、到完整功能里程碑或用户要求才 commit+push（不要碎提交）。
