# -*- coding: utf-8 -*-
"""
黄金市场雷达 — 打分纯函数层

本模块只依赖标准库(math/statistics), 序列输入允许 None 缺口(按日历对齐后
的常见形态), 所有因子函数都接收显式下标 i 以支持 --asof 历史回看。

设计约定(与 references/gold_framework.md 一致):
  - 位置类因子 → rolling_percentile (自排除滚动分位, 0-100)
  - 异动类因子 → zscore_tail + squash (tanh 压缩到 0-100)
  - 阈值类因子 → linear_map (分段线性插值, 两端沿末段斜率外推)
  - 聚合 → 缺失因子剔除并按剩余权重重归一, 返回覆盖度
"""

import math
from datetime import date

import gold_config as C

# ------------------------------------------------------------
# 序列基础工具
# ------------------------------------------------------------

def pct_returns(values, n=1):
    """n 日简单收益率序列(与输入等长, 不足处为 None)。None 缺口传播为 None。"""
    out = [None] * len(values)
    for i in range(len(values)):
        if i < n:
            continue
        a, b = values[i], values[i - n]
        if a is None or b is None or b == 0:
            continue
        out[i] = a / b - 1.0
    return out


def ma(values, n):
    """n 期简单均线(窗口内 None 剔除后不足 60% 样本则该点为 None)。"""
    out = [None] * len(values)
    for i in range(len(values)):
        if i < n - 1:
            continue
        win = [v for v in values[i - n + 1:i + 1] if v is not None]
        if len(win) >= n * 0.6:
            out[i] = sum(win) / len(win)
    return out


def realized_vol(values, window=20):
    """日收益率 std 的滚动序列(未年化; 展示层自行 ×√250)。
    需要约 window+1 个有效价格, 有效样本 < window 则 None。"""
    rets = pct_returns(values, 1)
    out = [None] * len(values)
    for i in range(len(values)):
        if i < window:
            continue
        win = [r for r in rets[i - window + 1:i + 1] if r is not None]
        if len(win) >= window:
            mean = sum(win) / len(win)
            var = sum((r - mean) ** 2 for r in win) / (len(win) - 1)
            out[i] = math.sqrt(var)
    return out


# ------------------------------------------------------------
# 归一化三件套
# ------------------------------------------------------------

def zscore_tail(values, i, window=250, min_obs=200):
    """values[i] 相对 (i-window, i] 前窗的 z 分数(自排除)。
    零方差或有效样本 < min_obs → None。"""
    if i <= 0 or i >= len(values) or values[i] is None:
        return None
    lo = max(0, i - window)
    win = [v for v in values[lo:i] if v is not None]
    if len(win) < min_obs:
        return None
    mean = sum(win) / len(win)
    var = sum((v - mean) ** 2 for v in win) / (len(win) - 1)
    std = math.sqrt(var)
    if std < 1e-12:
        return None
    return (values[i] - mean) / std


def rolling_percentile(values, i, window=750, min_obs=100, reverse=False):
    """values[i] 在前窗 (i-window, i) 内的自排除经验百分位(0-100, 含并列修正)。
    window<=0 表示全历史。reverse=True 为反向因子(值小者得分高)。
    当前值缺失或有效样本 < min_obs → None。"""
    if i <= 0 or i >= len(values) or values[i] is None:
        return None
    lo = 0 if window is None or window <= 0 else max(0, i - window)
    win = [v for v in values[lo:i] if v is not None]
    if len(win) < min_obs:
        return None
    v = values[i]
    below = sum(1 for w in win if w < v)
    ties = sum(1 for w in win if w == v)
    p = (below + 0.5 * ties) / len(win) * 100
    return round(100 - p if reverse else p, 1)


def squash(z, scale=2.0):
    """z 分数 → 0-100。squash(z)=100×(tanh(z/scale)+1)/2, z=0→50, z=±2→88.1/11.9。
    异动因子取 |z| 后单调递增: 越罕见分越高。"""
    if z is None:
        return None
    return round(100.0 * (math.tanh(z / scale) + 1.0) / 2.0, 1)


def linear_map(x, breakpoints, floor=None, cap=None):
    """分段线性插值映射到分数。breakpoints=[(x0,s0),...] 按 x 升序。
    两端沿末段斜率外推; floor/cap 提供时钳制。None/NaN → None。"""
    if x is None:
        return None
    try:
        if isinstance(x, float) and math.isnan(x):
            return None
    except Exception:
        return None
    xs = [bp[0] for bp in breakpoints]
    ss = [bp[1] for bp in breakpoints]
    if len(xs) < 2:
        s = float(ss[0])
    elif x <= xs[0]:
        s = ss[0] + (x - xs[0]) * ((ss[1] - ss[0]) / (xs[1] - xs[0]))
    elif x >= xs[-1]:
        s = ss[-1] + (x - xs[-1]) * ((ss[-1] - ss[-2]) / (xs[-1] - xs[-2]))
    else:
        s = ss[-1]
        for k in range(len(xs) - 1):
            if xs[k] <= x <= xs[k + 1]:
                frac = (x - xs[k]) / (xs[k + 1] - xs[k])
                s = ss[k] + frac * (ss[k + 1] - ss[k])
                break
    if floor is not None:
        s = max(floor, s)
    if cap is not None:
        s = min(cap, s)
    return s


# ------------------------------------------------------------
# 聚合与判读
# ------------------------------------------------------------

def aggregate(scores, weights, min_coverage=0.5):
    """加权合成。scores/weights 同键; 缺失( None )因子剔除并按剩余权重重归一。
    返回 (score|None, coverage, missing_keys)。coverage < min_coverage 时 score=None。"""
    num = den = cov = 0.0
    missing = []
    for k, w in weights.items():
        s = scores.get(k)
        if s is None:
            missing.append(k)
            continue
        num += s * w
        den += w
        cov += w
    total = float(sum(weights.values())) or 1.0
    coverage = cov / total
    if den <= 0 or coverage < min_coverage:
        return None, round(coverage, 3), missing
    return round(num / den, 1), round(coverage, 3), missing


def level_of(score, levels):
    """score → (标签, 信号灯, 行动提示)。levels=[(下限,标签,灯,提示),...] 降序,
    取 score 能匹配的最高档。score 为 None 或无匹配 → (None, '⚫', '')。"""
    if score is None:
        return None, "⚫", ""
    for floor, label, icon, action in levels:
        if score >= floor:
            return label, icon, action
    return None, "⚫", ""


# ------------------------------------------------------------
# 衍生序列
# ------------------------------------------------------------

def sge_premium_pct(sge_close, xau_close, usdcny, oz_g=31.1034768):
    """国内溢价% = 上海金 ÷ (伦敦金/盎司克重×USDCNY) − 1。
    任一输入缺失 → None。正值=国内金贵(抢金), 负值=国内折价。"""
    if sge_close is None or xau_close is None or usdcny in (None, 0):
        return None
    intl_cny_per_g = xau_close / oz_g * usdcny
    if intl_cny_per_g <= 0:
        return None
    return (sge_close / intl_cny_per_g - 1.0) * 100.0


def ath_drawdown(values, i=-1):
    """values[i] 相对其之前(含自身)历史最高的距离%: (close/ath − 1)×100, ≤0。
    距新高越近越接近 0。无有效数据 → None。"""
    if not values:
        return None
    idx = len(values) + i if i < 0 else i
    if idx < 0 or idx >= len(values) or values[idx] is None:
        return None
    hist = [v for v in values[:idx + 1] if v is not None]
    if not hist:
        return None
    return (values[idx] / max(hist) - 1.0) * 100.0


def premium_series(sge, xau, usdcny, oz_g=31.1034768):
    """逐日对齐后的国内溢价%序列(长度取三者最长, 逐点计算, 缺口为 None)。"""
    n = max(len(sge), len(xau), len(usdcny))

    def at(seq, k):
        return seq[k] if k < len(seq) else None

    return [sge_premium_pct(at(sge, k), at(xau, k), at(usdcny, k), oz_g)
            for k in range(n)]


def ratio_series(a, b):
    """逐点比值序列 a/b(长度取较长者, 任一缺失该点为 None)。"""
    n = max(len(a), len(b))

    def at(seq, k):
        return seq[k] if k < len(seq) else None

    out = []
    for k in range(n):
        x, y = at(a, k), at(b, k)
        out.append(x / y if x is not None and y not in (None, 0) else None)
    return out


def diff_series(a, b):
    """逐点差值序列 a−b(长度取较长者, 任一缺失该点为 None)。
    用于同日历序列的派生量, 如 通胀预期 = DGS10 − DFII10。"""
    n = max(len(a), len(b))

    def at(seq, k):
        return seq[k] if k < len(seq) else None

    return [at(a, k) - at(b, k)
            if at(a, k) is not None and at(b, k) is not None else None
            for k in range(n)]


# ------------------------------------------------------------
# 数据新鲜度(纯日期逻辑, 供 fetch 层调用)
# ------------------------------------------------------------

def days_between(latest, today):
    """'YYYY-MM-DD' 字符串日期差 today−latest(自然日)。解析失败 → None。"""
    try:
        return (date.fromisoformat(today) - date.fromisoformat(latest)).days
    except Exception:
        return None


def is_stale(latest, today, limit_days):
    """最新数据日距 today 超过 limit_days 视为陈旧(该源当日不可用)。
    latest 缺失/无法解析 → 视为陈旧。"""
    if not latest:
        return True
    d = days_between(latest, today)
    if d is None:
        return True
    return d < -1 or d > limit_days   # 未来数据(>1天)同样视为异常


# ------------------------------------------------------------
# 因子计算 — 双分数主入口(纯函数, 输入为按主日历对齐后的序列)
# series 键: core_close / sge_close / xau_close / xag_close / oil_close /
#            usdcny / real_rate / m2 / cftc_net / shares_total /
#            usd_idx / vix / dgs10 / news_count
# 主价格链 close = sge → etf → xau 首个可用序列(调用方已折算成 core_close)
# ------------------------------------------------------------

def _valid_count(values, i, window):
    lo = 0 if window is None or window <= 0 else max(0, i - window)
    return sum(1 for v in values[lo:i] if v is not None)


def _pct_factor(vals, i, window, min_obs, reverse=False):
    """滚动分位因子: 返回 (score, note)。note 解释缺失原因。"""
    if i >= len(vals) or vals[i] is None:
        return None, "上游缺失"
    if _valid_count(vals, i, window) < min_obs:
        return None, f"样本不足(<{min_obs})"
    return rolling_percentile(vals, i, window, min_obs, reverse), ""


def _wavg(parts):
    """parts=[(score, weight)] 缺失剔除重归一。全缺 → None。"""
    num = den = 0.0
    for s, w in parts:
        if s is None:
            continue
        num += s * w
        den += w
    return num / den if den > 0 else None


def compute_attention(series, i=-1):
    """关注分因子计算。series 为对齐后序列字典, i 为主日历下标(默认最后)。
    返回 {"score", "coverage", "missing", "factors": {k: {raw, z, score, note}}}"""
    idx = len(next(iter(series.values()))) + i if i < 0 else i
    close = series.get("core_close") or []
    factors = {}

    # 1/2. 单日与5日收益异动
    for key, n in (("ret1d_z", 1), ("ret5d_z", 5)):
        rets = pct_returns(close, n)
        z = zscore_tail(rets, idx, C.WIN_DAILY_Z, C.MIN_OBS_Z)
        note = ""
        if z is None:
            note = ("上游缺失" if idx >= len(rets) or rets[idx] is None
                    else f"样本不足(<{C.MIN_OBS_Z})")
        factors[key] = {"raw": (rets[idx] * 100 if idx < len(rets) and rets[idx] is not None else None),
                        "z": z, "score": squash(abs(z)) if z is not None else None, "note": note}

    # 3. 波动率突升 = 0.6×水平分位映射 + 0.4×分位抬升映射(缺失部分自动重归一)
    rv = realized_vol(close, C.RV_WINDOW)
    p_now = rolling_percentile(rv, idx, C.WIN_PCTILE, C.MIN_OBS_PCTILE)
    p_lag = rolling_percentile(rv, idx - C.RV_WINDOW, C.WIN_PCTILE, C.MIN_OBS_PCTILE) \
        if idx > C.RV_WINDOW else None
    lvl = linear_map(p_now, C.RV_PCTILE_MAP, 0, 100)
    dlt = linear_map(p_now - p_lag if (p_now is not None and p_lag is not None) else None,
                     C.VOL_DELTA_MAP, 0, 100)
    factors["vol_spike"] = {
        "raw": (rv[idx] * math.sqrt(250) * 100 if idx < len(rv) and rv[idx] is not None else None),
        "z": None,   # 本因子不是 z 型(水平分位+分位抬升合成), z 列显示 "—"
        "score": _wavg([(lvl, 0.6), (dlt, 0.4)]),
        "note": "" if (p_now is not None) else ("上游缺失" if idx >= len(rv) or rv[idx] is None
                                                else f"样本不足(<{C.MIN_OBS_PCTILE})")}

    # 4. 距历史新高
    dd = ath_drawdown(close, idx)
    factors["ath_prox"] = {
        "raw": dd, "z": None,
        "score": linear_map(dd / 100.0 if dd is not None else None,
                            C.ATH_PROX_MAP, C.ATH_PROX_FLOOR, C.ATH_PROX_CAP),
        "note": "" if dd is not None else "上游缺失"}

    # 5. 快讯热度: 当日黄金相关快讯条数的滚动分位(本地积累型, 冷启动缺失自动重归一)
    news = series.get("news_count") or []
    s, note = _pct_factor(news, idx, C.NEWS_WINDOW, C.MIN_NEWS_OBS)
    factors["news_heat"] = {"raw": news[idx] if idx < len(news) else None, "z": None,
                            "score": s,
                            "note": "" if s is not None
                            else ("快讯积累中" if "样本不足" in note else note)}

    scores = {k: v["score"] for k, v in factors.items()}
    total, cov, missing = aggregate(scores, C.ATT_WEIGHTS, C.MIN_COVERAGE)
    return {"score": total, "coverage": cov, "missing": missing, "factors": factors,
            "levels": C.ATTENTION_LEVELS}


def compute_temperature(series, i=-1):
    """温度分因子计算(位置类)。结构与 compute_attention 一致。"""
    idx = len(next(iter(series.values()))) + i if i < 0 else i
    close = series.get("core_close") or []
    xau = series.get("xau_close") or []
    factors = {}

    # 估值: 比值分位。方向见 FACTOR_META(热=值大越热, 冷=值大越冷)
    r = ratio_series(xau, series.get("m2") or [])
    s, note = _pct_factor(r, idx, C.WIN_LONG, C.MIN_OBS_LONG)
    factors["gold_m2"] = {"raw": r[idx] if idx < len(r) else None, "z": None,
                          "score": s, "note": note}

    r = ratio_series(xau, series.get("xag_close") or [])
    s, note = _pct_factor(r, idx, C.WIN_PCTILE, C.MIN_OBS_PCTILE, reverse=True)
    factors["gold_silver"] = {"raw": r[idx] if idx < len(r) else None, "z": None,
                              "score": s, "note": note}

    r = ratio_series(xau, series.get("oil_close") or [])
    s, note = _pct_factor(r, idx, C.WIN_PCTILE, C.MIN_OBS_PCTILE, reverse=True)
    factors["gold_oil"] = {"raw": r[idx] if idx < len(r) else None, "z": None,
                           "score": s, "note": note}

    rr = series.get("real_rate") or []
    s, note = _pct_factor(rr, idx, C.WIN_LONG, C.MIN_OBS_LONG, reverse=True)
    factors["real_rate"] = {"raw": rr[idx] if idx < len(rr) else None, "z": None,
                            "score": s, "note": note}

    # 趋势
    m = ma(close, 250)
    dev = (close[idx] / m[idx] - 1.0
           if idx < len(m) and m[idx] not in (None, 0) and close[idx] is not None else None)
    factors["ma250_dev"] = {"raw": dev * 100 if dev is not None else None, "z": None,
                            "score": linear_map(dev, C.MA250_DEV_MAP, 0, 100),
                            "note": "" if dev is not None else "样本不足(<250)"}

    for key, n in (("mom20", 20), ("mom60", 60), ("mom120", 120)):
        rets = pct_returns(close, n)
        s, note = _pct_factor(rets, idx, C.WIN_PCTILE, C.MIN_OBS_PCTILE)
        factors[key] = {"raw": (rets[idx] * 100 if idx < len(rets) and rets[idx] is not None else None),
                        "z": None, "score": s, "note": note}

    # 拥挤: 原始值越大越热(亢奋/踩踏风险)。
    # v2.1: ETF份额增速已移出分数(国内资金行为, 定价权原则), 仅作速览展示
    cf = series.get("cftc_net") or []
    s, note = _pct_factor(cf, idx, C.WIN_CFTC, C.MIN_OBS_CFTC)
    factors["cftc_net"] = {"raw": cf[idx] if idx < len(cf) else None, "z": None,
                           "score": s, "note": note}

    rv = realized_vol(close, C.RV_WINDOW)
    s, note = _pct_factor(rv, idx, C.WIN_PCTILE, C.MIN_OBS_PCTILE)
    factors["rv20_pct"] = {"raw": (rv[idx] * math.sqrt(250) * 100 if idx < len(rv) and rv[idx] is not None else None),
                           "z": None, "score": s, "note": note}

    # 宏观(v2): 金价的传统定价锚 — 美元/避险/通胀预期, GRAM 机会成本与风险维度
    usd = series.get("usd_idx") or []
    s, note = _pct_factor(usd, idx, C.WIN_PCTILE, C.MIN_OBS_PCTILE, reverse=True)
    factors["usd_idx"] = {"raw": usd[idx] if idx < len(usd) else None, "z": None,
                          "score": s, "note": note}

    vix = series.get("vix") or []
    s, note = _pct_factor(vix, idx, C.WIN_PCTILE, C.MIN_OBS_PCTILE)
    factors["vix"] = {"raw": vix[idx] if idx < len(vix) else None, "z": None,
                      "score": s, "note": note}

    be = diff_series(series.get("dgs10") or [], series.get("real_rate") or [])
    s, note = _pct_factor(be, idx, C.WIN_PCTILE, C.MIN_OBS_PCTILE)
    factors["breakeven"] = {"raw": be[idx] if idx < len(be) else None, "z": None,
                            "score": s, "note": note}

    scores = {k: v["score"] for k, v in factors.items()}
    total, cov, missing = aggregate(scores, C.TEMP_WEIGHTS, C.MIN_COVERAGE)
    return {"score": total, "coverage": cov, "missing": missing, "factors": factors,
            "levels": C.TEMPERATURE_LEVELS}
