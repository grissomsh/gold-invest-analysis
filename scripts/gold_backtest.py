# -*- coding: utf-8 -*-
"""
黄金市场雷达 — 分数校准回测(纯标准库)

借鉴 gold-agent 的 evaluate_signal_history + 显著性门控:
  - 逐历史日只用当日及以前的数据重算双分数(与实盘同一套 compute_* 函数)
  - 按判读分档分桶统计前向 5/20 日收益: 均值/中位/胜率/平均绝对波动
  - Spearman 秩IC: 分数与前向收益的单调关系(>0=分数有正向信息)
  - 显著性门控: 单档 n<BT_MIN_BUCKET 标注"样本不足"; 总样本<BT_MIN_TOTAL 整体仅供参考

边界说明: 这是"分数有没有信息量"的诊断, 不是策略盈亏回测 — 不模拟交易成本/
仓位/滑点, 也不构成对未来收益的预测。历史回放中 news_heat/share_growth 等本地
积累型因子自然缺失, 权重自动重归一(与真实降级同路径, 覆盖度如实偏低)。
"""

import json
import math
import os
from datetime import datetime

import gold_config as C
import gold_report as R
import gold_scoring as G

BACKTEST_JSON = os.path.join(C.WORKSPACE, "黄金回测.json")


# ------------------------------------------------------------
# 统计原语
# ------------------------------------------------------------

def _rank(vals):
    """平均秩(并列取平均, 1起)。None 传播为 None。"""
    idx = [i for i, v in enumerate(vals) if v is not None]
    out = [None] * len(vals)
    srt = sorted(idx, key=lambda i: vals[i])
    k = 0
    while k < len(srt):
        m = k
        while m + 1 < len(srt) and vals[srt[m + 1]] == vals[srt[k]]:
            m += 1
        avg = (k + m) / 2 + 1
        for j in range(k, m + 1):
            out[srt[j]] = avg
        k = m + 1
    return out


def spearman_ic(xs, ys, min_pairs=10):
    """Spearman 秩相关(忽略任一为 None 的样本对)。有效样本不足 → None。"""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < min_pairs:
        return None
    rx = _rank([p[0] for p in pairs])
    ry = _rank([p[1] for p in pairs])
    n = len(pairs)
    mx, my = sum(rx) / n, sum(ry) / n
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx <= 0 or dy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (dx * dy)


def forward_returns(close, horizons):
    """{k: [ret_i]} ret_i = close[i+k]/close[i] − 1, 样本不足该处 None。"""
    out = {}
    for k in horizons:
        r = [None] * len(close)
        for i in range(len(close) - k):
            a, b = close[i + k], close[i]
            if a is not None and b not in (None, 0):
                r[i] = a / b - 1.0
        out[k] = r
    return out


def _stats(rets):
    """非 None 收益列表 → 统计 dict; 空则 n=0"""
    vals = [r for r in rets if r is not None]
    if not vals:
        return {"n": 0}
    vals.sort()
    n = len(vals)
    return {"n": n,
            "mean": sum(vals) / n * 100.0,
            "median": (vals[n // 2] if n % 2 else (vals[n // 2 - 1]
                                                   + vals[n // 2]) / 2) * 100.0,
            "win": sum(1 for v in vals if v > 0) / n * 100.0,
            "avg_abs": sum(abs(v) for v in vals) / n * 100.0}


def bucket_stats(scores, ret_by_horizon, levels):
    """按判读分档分桶。levels 为降序档位表(与 LEVELS 常量一致), 内部转升序切片。
    返回 [{label, icon, floor, by_k: {k: stats}, enough}]"""
    lv = sorted(levels, key=lambda x: x[0])
    out = []
    for li, (floor, label, icon, _act) in enumerate(lv):
        hi = lv[li + 1][0] if li + 1 < len(lv) else 10**9
        mask = [s is not None and floor <= s < hi for s in scores]
        by_k = {k: _stats([r for r, m in zip(ret_by_horizon[k], mask) if m])
                for k in ret_by_horizon}
        n_total = max((by_k[k]["n"] for k in by_k), default=0)
        out.append({"label": label, "icon": icon, "floor": floor, "by_k": by_k,
                    "enough": n_total >= C.BT_MIN_BUCKET})
    return out


# ------------------------------------------------------------
# 主回放
# ------------------------------------------------------------

def replay(series, start):
    """逐历史日重算双分数。start 起算(暖机), 返回 (att_scores, temp_scores)。"""
    n = len(next(iter(series.values())))
    att, temp = [None] * n, [None] * n
    for i in range(max(0, start), n):
        att[i] = G.compute_attention(series, i)["score"]
        temp[i] = G.compute_temperature(series, i)["score"]
    return att, temp


def run_and_print(series, core, dates=None, horizons=None):
    """回放+分桶表+秩IC+JSON。dates 为主日历(可选, 仅用于表头区间标注)。"""
    horizons = horizons or C.BT_HORIZONS
    n = len(core)
    start = min(C.BT_START_IDX, n - 1)
    span = (f"{dates[start]} ~ {dates[n - 1]}" if dates and n - 1 < len(dates)
            else f"{n - start} 个观测")
    print(f"\n⏳ 回放打分({n - start} 日, 约需1分钟) …")
    att_s, temp_s = replay(series, start)
    rets = forward_returns(core, horizons)

    def hdr():
        h = R._pad("档位", 12) + "n".rjust(6)
        for k in horizons:
            h += f"{k}日均".rjust(10) + f"{k}日胜率".rjust(11)
        return h

    ic_block = {}
    blocks = {}
    for kind, scores, levels in (("attention", att_s, C.ATTENTION_LEVELS),
                                 ("temperature", temp_s, C.TEMPERATURE_LEVELS)):
        blocks[kind] = bucket_stats(scores, rets, levels)
        print(f"\n===== {'关注分' if kind == 'attention' else '温度分'} → 前向收益 ({span}) =====")
        print(hdr())
        for b in blocks[kind]:
            tag = "" if b["enough"] else " ←样本不足"
            row = R._pad(f"{b['label']}{b['icon']}", 12) \
                + str(b["by_k"][horizons[0]]["n"]).rjust(6)
            for k in horizons:
                st = b["by_k"][k]
                row += (f"{st['mean']:>+9.2f}%{st['win']:>10.0f}%"
                        if st["n"] else f"{'—':>9}{'—':>10}")
            print(row + tag)
        ic_block[kind] = {f"{k}d": spearman_ic(scores, rets[k])
                          for k in horizons}
        ics = ", ".join(f"{k}={v:+.3f}" if v is not None else f"{k}=—"
                        for k, v in ic_block[kind].items())
        print(f"秩IC: {ics}")

    n_total = sum(b["by_k"][horizons[0]]["n"] for b in blocks["attention"])
    if n_total < C.BT_MIN_TOTAL:
        print(f"\n⚠️ 总样本 {n_total} < {C.BT_MIN_TOTAL}: 以下结论仅供参考")
    print("说明: 历史回放中本地积累型因子(快讯/份额)缺失并重归一; "
          "本表是分数信息量诊断, 不构成对未来收益的预测。")

    os.makedirs(C.WORKSPACE, exist_ok=True)
    with open(BACKTEST_JSON, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": datetime.now().isoformat(timespec="seconds"),
                   "span": span, "n": n_total, "horizons": list(horizons),
                   "min_bucket": C.BT_MIN_BUCKET,
                   "ic": ic_block, "attention": blocks["attention"],
                   "temperature": blocks["temperature"]},
                  fh, ensure_ascii=False, indent=1, default=str)
    print(f"📄 回测数据: {BACKTEST_JSON}")
    return blocks
