#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
黄金市场雷达 — 打分模型离线回归

零第三方依赖(纯标准库), 不触网, 系统 python3 可直接运行:
    python3 tests/test_gold_scoring.py
退出码: 0=全部通过, 1=存在失败
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import gold_config as C          # noqa: E402
import gold_alert as AL          # noqa: E402
import gold_backtest as BT       # noqa: E402
import gold_data_store as store  # noqa: E402
import gold_report as R          # noqa: E402
import gold_scoring as G         # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        FAILS.append(name)
    print(("✅" if ok else "❌") + " " + name + ("" if ok else f"  ← {detail}"))


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ------------------------------------------------------------
# 1. rolling_percentile
# ------------------------------------------------------------
mono = [float(x) for x in range(1, 12)]
check("分位: 单调新高=100", G.rolling_percentile(mono, 10, 0, 5) == 100.0)
check("分位: 全并列=50", G.rolling_percentile([5.0] * 11, 10, 0, 5) == 50.0)
check("分位: 自排除(不含当前值)",
      G.rolling_percentile([1.0, 2.0, 3.0], 1, 0, 1) == 100.0,
      "前窗仅[1.0], v=2 应得100; 若含自身会得50")
check("分位: 样本不足=None", G.rolling_percentile([1.0, 2.0], 1, 0, 5) is None)
check("分位: 反向(最小值→100)",
      G.rolling_percentile([3.0, 2.0, 1.0], 2, 0, 1, reverse=True) == 100.0)
check("分位: 反向(最大值→0)",
      G.rolling_percentile([1.0, 2.0, 3.0], 2, 0, 1, reverse=True) == 0.0)
check("分位: 有限窗口",
      G.rolling_percentile(mono, 10, 3, 2) == 100.0, "窗口(7,8,9,10)中10为最大")
check("分位: 当前值缺失=None", G.rolling_percentile([1.0, None, 3.0], 1, 0, 1) is None)

# ------------------------------------------------------------
# 2. squash
# ------------------------------------------------------------
check("squash: z=0→50", G.squash(0.0) == 50.0)
check("squash: z=+2→88.1", G.squash(2.0) == 88.1, G.squash(2.0))
check("squash: z=-2→11.9", G.squash(-2.0) == 11.9, G.squash(-2.0))
check("squash: 单调", G.squash(1.0) < G.squash(2.0) < G.squash(3.0))
check("squash: 对称和=100", approx(G.squash(2.0) + G.squash(-2.0), 100.0, 1e-6))
check("squash: 有界", 0.0 <= G.squash(50.0) <= 100.0)
check("squash: None穿透", G.squash(None) is None)

# ------------------------------------------------------------
# 3. zscore_tail
# ------------------------------------------------------------
z = G.zscore_tail([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 5, window=5, min_obs=3)
check("z: 手算值", approx(z, (6 - 3) / math.sqrt(2.5), 1e-6), z)
check("z: 零方差=None",
      G.zscore_tail([5.0, 5.0, 5.0, 5.0], 3, window=3, min_obs=2) is None)
check("z: 样本不足=None",
      G.zscore_tail([1.0, 2.0], 1, window=5, min_obs=5) is None)
check("z: 自排除",
      approx(G.zscore_tail([1.0, 2.0, 3.0, 100.0], 3, window=3, min_obs=2),
             (100 - 2) / 1.0, 1e-6))

# ------------------------------------------------------------
# 4. aggregate
# ------------------------------------------------------------
s, cov, miss = G.aggregate({"a": 80.0, "b": 40.0}, {"a": 60, "b": 40})
check("聚合: 全在", s == 64.0 and cov == 1.0 and miss == [], (s, cov, miss))
s, cov, miss = G.aggregate({"a": 80.0}, {"a": 60, "b": 40})
check("聚合: 缺失重归一", s == 80.0 and approx(cov, 0.6) and miss == ["b"], (s, cov, miss))
s, cov, miss = G.aggregate({}, {"a": 60, "b": 40})
check("聚合: 全缺", s is None and cov == 0.0, (s, cov))
s, cov, miss = G.aggregate({"a": 50.0}, {"a": 30, "b": 70})
check("聚合: 覆盖度过低不给分", s is None and approx(cov, 0.3), (s, cov))

# ------------------------------------------------------------
# 5. level_of 边界
# ------------------------------------------------------------
lab, icon, _ = G.level_of(80.0, C.TEMPERATURE_LEVELS)
check("判读: 80.0→过热", lab == "过热" and icon == "🔴", (lab, icon))
check("判读: 79.99→偏热", G.level_of(79.99, C.TEMPERATURE_LEVELS)[0] == "偏热")
check("判读: 60→偏热", G.level_of(60.0, C.TEMPERATURE_LEVELS)[0] == "偏热")
check("判读: 40→中性", G.level_of(40.0, C.TEMPERATURE_LEVELS)[0] == "中性")
check("判读: 20→偏冷", G.level_of(20.0, C.TEMPERATURE_LEVELS)[0] == "偏冷")
check("判读: 0→过冷", G.level_of(0.0, C.TEMPERATURE_LEVELS)[0] == "过冷")
check("判读: 19.9→过冷", G.level_of(19.9, C.TEMPERATURE_LEVELS)[0] == "过冷")
check("判读: 关注70→重要异动",
      G.level_of(70.0, C.ATTENTION_LEVELS)[0] == "重要异动")
check("判读: 关注69.99→值得看一眼",
      G.level_of(69.99, C.ATTENTION_LEVELS)[0] == "值得看一眼")
check("判读: None→无档", G.level_of(None, C.TEMPERATURE_LEVELS) == (None, "⚫", ""))

# ------------------------------------------------------------
# 6. linear_map
# ------------------------------------------------------------
check("映射: 节点精确", G.linear_map(0.0, C.MA250_DEV_MAP) == 50.0)
check("映射: 末端沿斜率外推",
      approx(G.linear_map(-0.20, C.MA250_DEV_MAP), 0.0, 1e-9),
      G.linear_map(-0.20, C.MA250_DEV_MAP))
check("映射: floor钳制",
      G.linear_map(-0.30, C.ATH_PROX_MAP, C.ATH_PROX_FLOOR, C.ATH_PROX_CAP) == 5.0)
check("映射: cap钳制",
      G.linear_map(0.10, C.ATH_PROX_MAP, C.ATH_PROX_FLOOR, C.ATH_PROX_CAP) == 100.0)
check("映射: 区间内插值",
      approx(G.linear_map(-0.035, C.ATH_PROX_MAP), 70.0, 1e-9),
      G.linear_map(-0.035, C.ATH_PROX_MAP))
check("映射: None穿透", G.linear_map(None, C.MA250_DEV_MAP) is None)
check("映射: 越界外推有钳制(均线偏离+30%→100)",
      G.linear_map(0.30, C.MA250_DEV_MAP, 0, 100) == 100.0)
check("映射: 越界外推有钳制(分位差100→100)",
      G.linear_map(100.0, C.VOL_DELTA_MAP, 0, 100) == 100.0)
check("映射: 越界外推有钳制(下穿→0)",
      G.linear_map(-0.30, C.MA250_DEV_MAP, 0, 100) == 0.0)

# ------------------------------------------------------------
# 7. 国内溢价
# ------------------------------------------------------------
ozg = C.USD_PER_OUNCE
xau_flat = 100.0 / 7.13 * ozg            # 使 ¥/g 恰为 100 的伦敦金价
check("溢价: 平价=0", approx(G.sge_premium_pct(100.0, xau_flat, 7.13, ozg), 0.0, 1e-9))
check("溢价: +1%差价→1.0",
      approx(G.sge_premium_pct(101.0, xau_flat, 7.13, ozg), 1.0, 1e-6))
check("溢价: 缺汇率=None", G.sge_premium_pct(100.0, xau_flat, None, ozg) is None)
p = G.sge_premium_pct(995.05, 4454.23, 7.13, ozg)
check("溢价: 实测量级合理(|p|<10%)", p is not None and abs(p) < 10.0, p)

# ------------------------------------------------------------
# 8. 数据新鲜度
# ------------------------------------------------------------
check("新鲜度: days_between",
      G.days_between("2026-08-20", "2026-08-29") == 9)
check("新鲜度: 当日不陈旧", not G.is_stale("2026-08-28", "2026-08-29", 5))
check("新鲜度: 超限陈旧", G.is_stale("2026-08-20", "2026-08-29", 5))
check("新鲜度: 缺失视为陈旧", G.is_stale(None, "2026-08-29", 5))
check("新鲜度: 未来数据视为异常", G.is_stale("2026-09-05", "2026-08-29", 5))
check("新鲜度: 坏日期视为陈旧", G.is_stale("not-a-date", "2026-08-29", 5))

# ------------------------------------------------------------
# 8.5 方向感知动作文案 context_action
# ------------------------------------------------------------
CRASH = {"core_chg1d": 0.3, "xau_chg1d": -3.2}   # SGE收盘未含隔夜段, 伦敦金急跌
SURGE = {"core_chg1d": 0.5, "xau_chg1d": 2.5}
CALM = {"core_chg1d": 0.3, "xau_chg1d": 0.4}
check("文案: 急跌+偏热→不谈止盈",
      "止盈" not in R.context_action(CRASH, "偏热", "设止盈线")
      and R.context_action(CRASH, "偏热", "设止盈线").startswith("急跌方向未确认"))
check("文案: 急跌+偏热→利用反弹减仓, 不等破前低",
      "分批减仓" in R.context_action(CRASH, "偏热", "")
      and "勿等跌破前低" in R.context_action(CRASH, "偏热", ""))
check("文案: 急跌+偏热→快速收复=洗盘判据",
      "快速收复" in R.context_action(CRASH, "偏热", ""))
check("文案: 急跌+偏冷→小步分批",
      R.context_action(CRASH, "偏冷", "").startswith("急跌逼近左侧"))
check("文案: 急跌+中性→观望",
      R.context_action(CRASH, "中性", "").startswith("急跌观望"))
check("文案: 急跌+中性→方向判据=收复失地",
      "收复失地" in R.context_action(CRASH, "中性", ""))
check("文案: 急涨+偏热→计划性减仓",
      R.context_action(SURGE, "偏热", "").startswith("亢奋上涨"))
check("文案: 急涨+中性→不加仓",
      R.context_action(SURGE, "中性", "").startswith("急涨不加仓"))
check("文案: 平静→沿用档位默认", R.context_action(CALM, "偏热", "BASE") == "BASE")
check("文案: 主链缺失仍看伦敦金",
      R.context_action({"core_chg1d": None, "xau_chg1d": -3.0},
                       "偏热", "BASE").startswith("急跌"))
check("文案: 全缺→默认",
      R.context_action({"core_chg1d": None, "xau_chg1d": None},
                       "偏热", "BASE") == "BASE")

# ------------------------------------------------------------
# 9. 配置自检
# ------------------------------------------------------------
check("配置: 关注权重和=100", sum(C.ATT_WEIGHTS.values()) == 100)
check("配置: 温度权重和=100", sum(C.TEMP_WEIGHTS.values()) == 100)
grp = {}
for k, w in C.TEMP_WEIGHTS.items():
    grp[C.FACTOR_META[k]["组"]] = grp.get(C.FACTOR_META[k]["组"], 0) + w
check("配置: 温度分组权重一致",
      {g: round(w * 100) for g, w in C.TEMP_GROUPS} == grp,
      (dict(C.TEMP_GROUPS), grp))
check("配置: 权重键均有元数据",
      all(k in C.FACTOR_META for k in list(C.ATT_WEIGHTS) + list(C.TEMP_WEIGHTS)))
check("配置: 元数据无孤儿键",
      all(k in C.ATT_WEIGHTS or k in C.TEMP_WEIGHTS for k in C.FACTOR_META))
check("配置: 判读档位降序",
      all(C.ATTENTION_LEVELS[i][0] > C.ATTENTION_LEVELS[i + 1][0]
          for i in range(len(C.ATTENTION_LEVELS) - 1))
      and all(C.TEMPERATURE_LEVELS[i][0] > C.TEMPERATURE_LEVELS[i + 1][0]
              for i in range(len(C.TEMPERATURE_LEVELS) - 1)))
for nm, mp in [("RV_PCTILE_MAP", C.RV_PCTILE_MAP), ("VOL_DELTA_MAP", C.VOL_DELTA_MAP),
               ("ATH_PROX_MAP", C.ATH_PROX_MAP), ("MA250_DEV_MAP", C.MA250_DEV_MAP)]:
    check(f"配置: {nm} 升序", all(mp[i][0] < mp[i + 1][0] for i in range(len(mp) - 1)))

# ------------------------------------------------------------
# 10. 金样场景 — 种子随机游走 + 注入+8σ单日
# ------------------------------------------------------------


def build_scene(n=900, shock=False):
    """构造对齐后的序列字典(模拟真实量纲)。shock=True 在最后一天注入+8%跳空。"""
    rnd = random.Random(7)

    def walk(start, mu, sig, lo=None):
        out, v = [], start
        for _ in range(n):
            v = max(v * (1 + mu + rnd.gauss(0, sig)), lo or 1e-6)
            out.append(v)
        return out

    def ffill_by(step, base, drift, sig):
        """按 step 间隔更新的前向填充序列(模拟周频CFTC/月频M2)"""
        raw, v = [], base
        for k in range(n):
            if k % step == 0:
                v = v * (1 + drift + rnd.gauss(0, sig))
            raw.append(v)
        return raw

    close = walk(300.0, 0.0005, 0.008, lo=100.0)
    if shock:
        close[-1] *= 1.08
    else:
        # "安静场景"末日强制为平常的一天(漂移游走的随机末日可能恰逢大波动日,
        # 那种日子得高分是模型的正确行为, 不该用来当安静基线)
        close[-1] = close[-2] * 1.0005
    m2_raw = ffill_by(21, 15000.0, 0.003, 0.004)   # 日历化前已逐日前向填充
    cftc_raw = ffill_by(5, 150000.0, 0.0005, 0.02)
    return {
        "core_close": close,
        "sge_close": walk(700.0, 0.0005, 0.008, lo=200.0),
        "xau_close": walk(2000.0, 0.0005, 0.008, lo=500.0),
        "xag_close": walk(30.0, 0.0005, 0.012, lo=5.0),
        "oil_close": walk(80.0, 0.0004, 0.015, lo=20.0),
        "etf_close": walk(8.0, 0.0005, 0.009, lo=2.0),
        "etf_turnover": [max(rnd.gauss(3.0, 1.2), 0.1) for _ in range(n)],
        "usdcny": walk(7.10, 0.00005, 0.0015, lo=5.0),
        "real_rate": walk(2.0, 0.0, 0.02, lo=-1.0),
        "m2": m2_raw,
        "cftc_net": cftc_raw,
        "shares_total": walk(8e9, 0.0002, 0.001, lo=1e8),
        "usd_idx": walk(118.0, 0.0001, 0.004, lo=80.0),
        "vix": walk(15.0, 0.0, 0.03, lo=9.0),
        "dgs10": walk(4.2, 0.0, 0.01, lo=0.5),
        "news_count": [float(max(0, int(rnd.gauss(1.5, 1.3)))) for _ in range(n)],
    }


scene = build_scene()
att = G.compute_attention(scene)
temp = G.compute_temperature(scene)
check("金样: 覆盖度=1.0", att["coverage"] == 1.0 and temp["coverage"] == 1.0,
      (att["coverage"], temp["coverage"], att["missing"], temp["missing"]))
check("金样: 双分数∈[0,100]",
      0 <= (att["score"] or -1) <= 100 and 0 <= (temp["score"] or -1) <= 100,
      (att["score"], temp["score"]))
check("金样: 中性序列无过热/重要异动",
      att["score"] < 70 and temp["score"] < 80, (att["score"], temp["score"]))

scene_shock = build_scene(shock=True)
scene_shock["etf_turnover"][-1] = 12.0   # 异动日成交放量(确定性, 避免随机项压低分数)
att2 = G.compute_attention(scene_shock)
temp2 = G.compute_temperature(scene_shock)
check("金样: +8σ单日→关注分>80", (att2["score"] or 0) > 80, att2["score"])
check("金样: +8σ单日→温度分<90", (temp2["score"] or 0) < 90, temp2["score"])
check("金样: 冲击后双分数仍在[0,100]",
      0 <= (att2["score"] or -1) <= 100 and 0 <= (temp2["score"] or -1) <= 100)

short = {"core_close": [300.0 + k for k in range(50)]}
t3 = G.compute_temperature(short)
a3 = G.compute_attention(short)
check("短样本: 分数置None", t3["score"] is None and a3["score"] is None,
      (t3["score"], a3["score"]))
check("短样本: 覆盖度过低", t3["coverage"] < 0.5 and a3["coverage"] < 0.5,
      (t3["coverage"], a3["coverage"]))

# 缺口传播: 价格序列中间挖洞, 因子不崩溃且缺口后仍可恢复计算
holed = build_scene(n=400)
for k in range(200, 220):
    holed["core_close"][k] = None
a4 = G.compute_attention(holed, i=399)
check("缺口: 不崩溃且有分", a4["score"] is not None and 0 <= a4["score"] <= 100,
      (a4["score"], a4["missing"]))

# ath_drawdown / 序列工具
check("ATH: 创新高=0", G.ath_drawdown([1.0, 2.0, 3.0], 2) == 0.0)
check("ATH: 回撤距离", approx(G.ath_drawdown([1.0, 3.0, 2.0], 2), -33.3333, 1e-3))
check("ATH: 空序列=None", G.ath_drawdown([], 0) is None)
rv = G.realized_vol([1.0, 1.01, 0.99, 1.02, 1.0, 0.98, 1.01], window=5)
check("RV: 正值且非None", rv[-1] is not None and rv[-1] > 0)
mm = G.ma([1.0, 2.0, 3.0, 4.0, 5.0], 5)
check("MA: 末点=3", approx(mm[4], 3.0, 1e-9) and mm[0] is None)

# ------------------------------------------------------------
# 11. v2 新因子: 快讯热度 / 金油比 / 美元 / VIX / 通胀预期
# ------------------------------------------------------------
check("diff: 逐点相减与缺口",
      G.diff_series([1.0, 2.0, None, 4.0], [0.5, None, 3.0, 1.0]) == [0.5, None, None, 3.0])

sc = build_scene(n=400)
base_t = G.compute_temperature(sc)
check("宏观: 场景四因子全部有分",
      all(base_t["factors"][k]["score"] is not None
          for k in ("gold_oil", "usd_idx", "vix", "breakeven")),
      {k: base_t["factors"][k]["score"] for k in ("gold_oil", "usd_idx", "vix", "breakeven")})

hi_vix = G.compute_temperature({**sc, "vix": sc["vix"][:-1] + [60.0]})
check("宏观: VIX末端飙至60→vix分=100(大→热)",
      hi_vix["factors"]["vix"]["score"] == 100.0, hi_vix["factors"]["vix"]["score"])
hi_usd = G.compute_temperature({**sc, "usd_idx": sc["usd_idx"][:-1] + [200.0]})
check("宏观: 美元末端飙至200→usd分=0(大→冷)",
      hi_usd["factors"]["usd_idx"]["score"] == 0.0, hi_usd["factors"]["usd_idx"]["score"])
oil_crash = G.compute_temperature({**sc, "oil_close": sc["oil_close"][:-1] + [1.0]})
check("估值: 油价崩盘→金油比创高→分=0(反向)",
      oil_crash["factors"]["gold_oil"]["score"] == 0.0, oil_crash["factors"]["gold_oil"]["score"])
hi_be = G.compute_temperature({**sc, "dgs10": sc["dgs10"][:-1] + [9.0]})
check("宏观: 名义利率飙升→通胀预期创高→分=100(大→热)",
      hi_be["factors"]["breakeven"]["score"] == 100.0, hi_be["factors"]["breakeven"]["score"])

hi_news = G.compute_attention({**sc, "news_count": sc["news_count"][:-1] + [12.0]})
check("关注: 快讯条数创高→news_heat=100",
      hi_news["factors"]["news_heat"]["score"] == 100.0, hi_news["factors"]["news_heat"]["score"])
cold_news = G.compute_attention({"core_close": [300.0 + k for k in range(300)],
                                 # 对齐后的冷启动形态: 积累期之前为 None(缺失≠0)
                                 "news_count": [None] * 290 + [1.0] * 10})
check("关注: 快讯冷启动→缺失且标注积累中",
      cold_news["factors"]["news_heat"]["score"] is None
      and cold_news["factors"]["news_heat"]["note"] == "快讯积累中",
      cold_news["factors"]["news_heat"])
check("关注: 快讯缺失→权重重归一",
      approx(cold_news["coverage"], 1.0 - 15 / 100.0, 1e-9), cold_news["coverage"])

# ------------------------------------------------------------
# 12. 回测统计原语(纯 stdlib)
# ------------------------------------------------------------
check("IC: 完全单调=+1",
      approx(BT.spearman_ic([1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                            [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]), 1.0, 1e-9))
check("IC: 完全反向=-1",
      approx(BT.spearman_ic([1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                            [100, 90, 80, 70, 60, 50, 40, 30, 20, 10]), -1.0, 1e-9))
check("IC: 并列取平均秩",
      approx(BT.spearman_ic([1, 1, 2, 2, 3, 3, 4, 4, 5, 5],
                            [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]), 1.0, 1e-9))
check("IC: 样本不足=None", BT.spearman_ic([1, 2, 3], [3, 2, 1]) is None)
fr = BT.forward_returns([100.0, 110.0, 121.0, 100.0], (1, 2))
check("前向收益: 1日", approx(fr[1][0], 0.10, 1e-9), fr[1])
check("前向收益: 尾部样本不足=None", fr[2][3] is None and fr[2][2] is None)
st = BT._stats([0.05, -0.03, 0.10])
check("分桶统计: 均值/胜率",
      st["n"] == 3 and approx(st["mean"], 4.0, 1e-6)
      and approx(st["win"], 200.0 / 3, 0.01), st)
bs = BT.bucket_stats([10.0, 50.0, 90.0], {5: [0.01, -0.02, 0.03]},
                     C.ATTENTION_LEVELS)
check("分桶: 三档各1条且标注样本不足",
      [b["by_k"][5]["n"] for b in bs] == [1, 1, 1]
      and not all(b["enough"] for b in bs), bs)

# ------------------------------------------------------------
# 13. 多空速览与推送规则(纯逻辑, 不触网)
# ------------------------------------------------------------
def _f(name, key, score, raw):
    return {"名": name, "key": key, "raw": raw, "score": score, "z": None,
            "方向": "", "窗": "", "组": "", "weight": 0, "note": ""}


def _rep_bb(att_f, temp_f):
    return {"attention": {"factors": att_f}, "temperature": {"factors": temp_f},
            "market": {}}


verdict, bull, bear = R.bull_bear(_rep_bb(
    [_f("20日动量分位", "mom20", 75, 1.2)],
    [_f("金价/M2分位", "gold_m2", 88, 0.00061),
     _f("实际利率分位", "real_rate", 70, 1.2),
     _f("美元指数分位", "usd_idx", 65, 118.0)]))
check("多空: 多3空1→偏多", verdict == "偏多" and len(bull) == 3 and len(bear) == 1,
      (verdict, bull, bear))
verdict, bull, bear = R.bull_bear(_rep_bb(
    [], [_f("金价/M2分位", "gold_m2", 88, 0.00061),
         _f("波动率分位", "rv20_pct", 90, 41.2)]))
check("多空: 估值贵+高波动→偏空", verdict == "偏空" and len(bear) == 2, (verdict, bear))
verdict, bull, bear = R.bull_bear(_rep_bb(
    [], [_f("金油比分位", "gold_oil", 90, 55.0)]))
check("多空: 金油比反向高分=偏多证据", len(bull) == 1 and not bear, (bull, bear))
verdict, bull, bear = R.bull_bear(_rep_bb(
    [_f("20日动量分位", "mom20", 75, 1.2)],
    [_f("金价/M2分位", "gold_m2", 88, 0.00061)]))
check("多空: 多1空1→拉锯", verdict == "拉锯", verdict)
verdict, bull, bear = R.bull_bear(_rep_bb(
    [_f("快讯热度分位", "news_heat", 95, 8.0)], []))
check("多空: 中性事件因子不进卡", verdict == "拉锯" and not bull and not bear,
      (verdict, bull, bear))

AL._prev_scores = lambda today: (55.0, 55.0)      # 前日: 关注55(值得看) 温55(中性)
rep_a = {"attention": {"score": 72.0, "label": "重要异动", "icon": "🔴"},
         "temperature": {"score": 55.0, "label": "中性", "icon": "⚪"},
         "market": {"core_chg1d": 0.3, "xau_chg1d": 0.4}}
fires = AL.evaluate(rep_a, "2026-08-30")
check("推送: 关注升档触发", any(k == AL.RULE_ATT_UP for k, _ in fires), fires)
check("推送: 温度未变档不触发", not any(k == AL.RULE_TEMP for k, _ in fires), fires)
check("推送: 无大异动不触发", not any(k == AL.RULE_BIG_MOVE for k, _ in fires), fires)
rep_b = {**rep_a, "market": {"core_chg1d": -3.2, "xau_chg1d": 0.3}}
fires = AL.evaluate(rep_b, "2026-08-30")
check("推送: 急跌+关注≥40→大异动触发",
      any(k == AL.RULE_BIG_MOVE for k, _ in fires), fires)
rep_c = {"attention": {"score": 30.0, "label": "日常波动", "icon": "⚪"},
         "temperature": {"score": 65.0, "label": "偏热", "icon": "🟠"},
         "market": {"core_chg1d": -3.2, "xau_chg1d": 0.3}}
fires = AL.evaluate(rep_c, "2026-08-30")
check("推送: 温度变档触发/关注低+急跌不触发大异动",
      any(k == AL.RULE_TEMP for k, _ in fires)
      and not any(k == AL.RULE_BIG_MOVE for k, _ in fires), fires)
AL._prev_scores = lambda today: (None, None)
check("推送: 首日无基线不触发档位规则",
      AL.evaluate({**rep_a, "market": {"core_chg1d": 0.1, "xau_chg1d": 0.1}},
                  "2026-08-30") == [])

import tempfile                                     # noqa: E402
_tmp = tempfile.mkdtemp(prefix="gold_test_")
_orig_ws, _orig_db = C.WORKSPACE, C.DB_PATH         # 先存原值(模块单例, 重导入无效)
C.WORKSPACE, C.DB_PATH = _tmp, os.path.join(_tmp, "t.db")
store.upsert_news_count("2026-08-29", 3, "[]")
store.upsert_news_count("2026-08-29", 5, "[]")      # 幂等覆盖
check("存储: 快讯落库与覆盖",
      store.news_count_series() == (["2026-08-29"], [5.0]),
      store.news_count_series())
check("存储: 未积累日期不在序列(缺失≠0)",
      store.news_count_series()[0] != ["2026-08-28"])
store.mark_alert_sent("r1", "2026-08-30")
check("存储: 推送去重(当日已发)",
      store.alert_sent_today("r1", "2026-08-30")
      and not store.alert_sent_today("r1", "2026-08-31")
      and not store.alert_sent_today("r2", "2026-08-30"))
C.WORKSPACE, C.DB_PATH = _orig_ws, _orig_db          # 还原, 防污染后续断言

# ------------------------------------------------------------
print()
if FAILS:
    print(f"❌ {len(FAILS)} 项失败: {FAILS}")
    sys.exit(1)
print("✅ 全部通过")
sys.exit(0)
