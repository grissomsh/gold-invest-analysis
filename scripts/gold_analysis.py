# -*- coding: utf-8 -*-
"""
黄金市场雷达 — 主流程与 CLI

流水线: fetch(gold_fetch) → 主日历+对齐(本文件) → 因子/双分数(gold_scoring)
        → 控制台/HTML/JSON(gold_report) → SQLite 积累(gold_data_store)

用法:
    python3 gold_analysis.py                    # 全量: 双分数 + 表格 + HTML + JSON
    python3 gold_analysis.py --healthcheck      # 数据源自检
    python3 gold_analysis.py --no-html          # 只要表格+JSON
    python3 gold_analysis.py --json-only        # 只要 JSON
    python3 gold_analysis.py --debug            # 每因子原始值/分位/缺失原因
    python3 gold_analysis.py --asof 2026-06-30  # 历史截面回看
    python3 gold_analysis.py --backfill-shares 60  # 首次运行回补ETF份额
    python3 gold_analysis.py --stats            # 本地数据库状态
    python3 gold_analysis.py --backtest         # 分数→未来收益 校准回测
    python3 gold_analysis.py --alert            # 运行后按规则推送提醒(需env配key)
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta

import gold_alert as AL
import gold_backtest as BT
import gold_config as C
import gold_data_store as store
import gold_fetch as F
import gold_report as R
import gold_scoring as G

# 每源前向填充容差(自然日): 日历取SGE, 其他源按各自发布节奏允许缺口。
# m2/usdcny 的容差须覆盖其发布滞后(FRED DEXCHUS 滞后约5-6个交易日;
# M2 月频观测日期距最后可用日可达约2个月), 否则最新点会被误杀为缺失
ALIGN_TOL = {
    "sge": 0, "etf": 3, "xau": 4, "xag": 4, "oil": 4, "usdcny": 12,
    "treasury": 7, "real_rate": 10, "m2": 75, "cftc": 21, "shares": 5,
    "usd_idx": 12,     # DTWEXBGS 周频发布, 容差必须跨周
    "vix": 7, "dgs10": 7,
    "news": 3,         # 快讯按抓取当日落库, 容差桥接周末(当日总有自身条目)
}


def _shift(d, days):
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


def now_ts():
    """报告生成时刻。注意必须用 datetime(带时间) 而非 date——date.strftime
    对 %H:%M:%S 永远输出 00:00:00。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def align_series(dates, values, calendar, tol, lag=0):
    """前向填充对齐: 日历日 c 取最新观测 obs≤c−lag 且 c−obs≤tol, 否则 None。
    用于把不同发布节奏(日频SGE/周频CFTC/月频M2)铺到同一条日历上。"""
    out = [None] * len(calendar)
    j = -1
    for i, c in enumerate(calendar):
        bound = _shift(c, -lag)
        while j + 1 < len(dates) and dates[j + 1] <= bound:
            j += 1
        if j >= 0:
            gap = G.days_between(dates[j], c)          # c − obs ≥ 0
            if gap is not None and gap <= tol:
                out[i] = values[j]
    return out


def _pct(a, b):
    return (a / b - 1.0) * 100.0 if a is not None and b not in (None, 0) else None


def update_news(today_ref, asof=None):
    """快讯落库: 抓取→统计当日条数→存 news_log(0 也是真实观测, 只有源全挂不落)。
    返回 (当日条数, 近3日rows, 成功源数)。--asof 回看不消费"现在"的快讯。"""
    if asof:
        return None, [], 0
    try:
        rows, n_ok = F.fetch_news(max_age_days=3)
    except Exception:                                 # noqa: BLE001
        return None, [], 0
    if n_ok == 0:
        return None, rows, 0
    todays = [r for r in rows if r["时间"][:10] == today_ref]
    try:
        store.upsert_news_count(today_ref, len(todays),
                                json.dumps(todays, ensure_ascii=False))
    except Exception:                                 # noqa: BLE001
        pass
    return len(todays), rows, n_ok


def update_shares(calendar, backfill=0):
    """ETF份额落库: 每日增量(沪市当日快照+深市区间) + 可选历史回补。
    返回落库条数(本次新增写入量, 含覆盖)。"""
    existing = store.etf_share_dates()
    latest_db = max(existing) if existing else None
    rows = []

    # 深市: 区间接口一次拉(从上一落库日至今)
    start = latest_db or _shift(calendar[-1], -max(backfill, 7) - 5)
    try:
        rows += F.fetch_etf_shares_szse(start, calendar[-1])
    except Exception:
        pass

    # 沪市: 逐日快照。日常只看最近3个日历日、最多落2日(盘后延迟容错);
    # backfill>0 时从最新日历日向前回补 N 日(逐日拉取, 较慢)
    n_days = max(2, int(backfill))
    candidates = list(reversed(calendar)) if backfill > 0 else list(reversed(calendar))[:3]
    fetched = 0
    for d in candidates:
        if d in existing and backfill <= 0:
            continue
        try:
            snap = F.fetch_etf_shares_sse(d.replace("-", ""))
        except Exception:
            snap = {}
        rows += [(d, c, v) for c, v in snap.items()]
        fetched += 1
        if fetched >= n_days:
            break
    store.upsert_etf_shares(rows)
    return len(rows)


def run(make_html=True, json_only=False, debug=False, asof=None,
        backfill_shares=0, backtest=False, alert=False):
    """主流程。返回完整报告 dict; 数据严重不可用时返回 None。"""
    today = date.today().isoformat()
    today_ref = asof or today                     # 陈旧判定的参照日

    # ---- 1. 拉取全部数据源 ----
    print("⏳ 拉取数据源 …")
    sge_d, sge_v = F.fetch_sge()
    xau_d, xau_v = F.fetch_xau()
    xag_d, xag_v = F.fetch_xau(C.XAG_SYMBOL)
    oil_d, oil_v = F.fetch_xau(C.OIL_SYMBOL)
    etf_d, etf = F.fetch_etf_daily(C.ETF_PRIMARY)
    etf_src = C.ETF_PRIMARY
    if not etf_d:                                 # 主ETF失败 → 深市备选
        etf_d, etf = F.fetch_etf_daily(C.ETF_BACKUP)
        etf_src = C.ETF_BACKUP if etf_d else etf_src
    tre_d, tre = F.fetch_treasury()
    rr_d, rr_v = F.fetch_fred(C.FRED_SERIES["real_rate"])
    m2_d, m2_v = F.fetch_fred(C.FRED_SERIES["m2"])
    fx_d, fx_v = F.fetch_fred(C.FRED_SERIES["usdcny"])
    usd_d, usd_v = F.fetch_fred(C.FRED_SERIES["usd_idx"])
    vix_d, vix_v = F.fetch_fred(C.FRED_SERIES["vix"])
    dgs_d, dgs_v = F.fetch_fred(C.FRED_SERIES["dgs10"])
    cf_d, cf_v = F.fetch_cftc_gold()

    # --asof: 先把各序列截断到 asof(不透支未来数据), 新鲜度才有意义
    if asof:
        def _cut(d, v):
            keep = [i for i, x in enumerate(d) if x <= asof]
            return ([d[i] for i in keep],
                    [v[i] for i in keep] if isinstance(v, list)
                    else {k: [s[i] for i in keep] for k, s in v.items()})
        sge_d, sge_v = _cut(sge_d, sge_v)
        xau_d, xau_v = _cut(xau_d, xau_v)
        xag_d, xag_v = _cut(xag_d, xag_v)
        oil_d, oil_v = _cut(oil_d, oil_v)
        etf_d, etf = _cut(etf_d, etf)
        tre_d, tre = _cut(tre_d, tre)
        rr_d, rr_v = _cut(rr_d, rr_v)
        m2_d, m2_v = _cut(m2_d, m2_v)
        fx_d, fx_v = _cut(fx_d, fx_v)
        usd_d, usd_v = _cut(usd_d, usd_v)
        vix_d, vix_v = _cut(vix_d, vix_v)
        dgs_d, dgs_v = _cut(dgs_d, dgs_v)
        cf_d, cf_v = _cut(cf_d, cf_v)

    statuses = {
        "sge": F.source_status(sge_d, "sge", today_ref),
        "xau": F.source_status(xau_d, "xau", today_ref),
        "xag": F.source_status(xag_d, "gold_silver", today_ref),
        "oil": F.source_status(oil_d, "oil", today_ref),
        "etf": F.source_status(etf_d, "etf", today_ref),
        "treasury": F.source_status(tre_d, "treasury", today_ref),
        "real_rate": F.source_status(rr_d, "real_rate", today_ref),
        "m2": F.source_status(m2_d, "m2", today_ref),
        "usdcny": F.source_status(fx_d, "usdcny", today_ref),
        "usd_idx": F.source_status(usd_d, "usd_idx", today_ref),
        "vix": F.source_status(vix_d, "vix", today_ref),
        "dgs10": F.source_status(dgs_d, "dgs10", today_ref),
        "cftc": F.source_status(cf_d, "cftc", today_ref),
    }

    # 东财不可用 → 腾讯K线兜底。换手率自算 = 成交量(手)×100 ÷ 流通份额(份),
    # 份额用本地SQLite累积序列(需 --backfill-shares 预置历史)
    etf_fallback = False
    if not etf_d:
        etf_d, etf_c, tv_v = F.fetch_etf_kline_tencent(C.ETF_PRIMARY)
        if etf_d:
            etf = {"close": etf_c, "turnover": [None] * len(etf_d),
                   "vol_hand": tv_v}
            etf_fallback = True
            statuses["etf"] = {**statuses["etf"], "ok": True, "n": len(etf_d),
                               "note": "东财不可用, 腾讯K线兜底(换手率自算)"}

    for k, st in statuses.items():
        mark = "✅" if st["ok"] else "⚠️ "
        print(f"   {mark} {k}: {st['n']}行, 最后 {st['latest']} {st['note']}")

    # ---- 1.5 陈旧/失败源整体剔除(过期视为缺失, 因子层权重重归一) ----
    def _gate(key, d, v):
        return (d, v) if st_ok(statuses[key]) else ([], [])

    sge_d, sge_v = _gate("sge", sge_d, sge_v)
    xau_d, xau_v = _gate("xau", xau_d, xau_v)
    xag_d, xag_v = _gate("xag", xag_d, xag_v)
    oil_d, oil_v = _gate("oil", oil_d, oil_v)
    etf_d, etf = (etf_d, etf) if st_ok(statuses["etf"]) else ([], {})
    tre_d, tre = _gate("treasury", tre_d, tre)
    rr_d, rr_v = _gate("real_rate", rr_d, rr_v)
    m2_d, m2_v = _gate("m2", m2_d, m2_v)
    fx_d, fx_v = _gate("usdcny", fx_d, fx_v)
    usd_d, usd_v = _gate("usd_idx", usd_d, usd_v)
    vix_d, vix_v = _gate("vix", vix_d, vix_v)
    dgs_d, dgs_v = _gate("dgs10", dgs_d, dgs_v)
    cf_d, cf_v = _gate("cftc", cf_d, cf_v)

    # ---- 2. 主日历(SGE → ETF → XAU 兜底), 截断到 asof ----
    cal_src = "sge"
    calendar = [d for d in sge_d if st_ok(statuses["sge"])]
    if not calendar:
        cal_src, calendar = "etf", [d for d in etf_d if st_ok(statuses["etf"])]
    if not calendar:
        cal_src, calendar = "xau", [d for d in xau_d if st_ok(statuses["xau"])]
    if not calendar:
        print("❌ 三条价格链(SGE/ETF/XAU)均不可用, 无法计算")
        return None
    if asof:
        calendar = [d for d in calendar if d <= asof]
        if len(calendar) < 30:
            print(f"❌ --asof {asof} 之后有效样本过少({len(calendar)}行)")
            return None
    idx = len(calendar) - 1
    rep_date = calendar[idx]

    # ---- 3. 对齐到主日历 ----
    sge_a = align_series(sge_d, sge_v, calendar, ALIGN_TOL["sge"])
    xau_a = align_series(xau_d, xau_v, calendar, ALIGN_TOL["xau"])
    xag_a = align_series(xag_d, xag_v, calendar, ALIGN_TOL["xag"])
    oil_a = align_series(oil_d, oil_v, calendar, ALIGN_TOL["oil"])
    fx_a = align_series(fx_d, fx_v, calendar, ALIGN_TOL["usdcny"])
    rr_a = align_series(rr_d, rr_v, calendar, ALIGN_TOL["real_rate"])
    m2_a = align_series(m2_d, m2_v, calendar, ALIGN_TOL["m2"], lag=C.PUBLISH_LAGS["m2"])
    usd_a = align_series(usd_d, usd_v, calendar, ALIGN_TOL["usd_idx"],
                         lag=C.PUBLISH_LAGS["usd_idx"])
    vix_a = align_series(vix_d, vix_v, calendar, ALIGN_TOL["vix"],
                         lag=C.PUBLISH_LAGS["vix"])
    dgs_a = align_series(dgs_d, dgs_v, calendar, ALIGN_TOL["dgs10"],
                         lag=C.PUBLISH_LAGS["dgs10"])
    cf_a = align_series(cf_d, cf_v, calendar, ALIGN_TOL["cftc"], lag=C.PUBLISH_LAGS["cftc"])
    be_a = G.diff_series(dgs_a, rr_a)      # 通胀预期 = 名义10Y − 实际10TIPS

    # ---- 4. ETF份额: 增量落库 + 时序 ----
    try:
        n_written = update_shares(calendar, backfill_shares)
        print(f"   ✅ ETF份额落库 {n_written} 条")
    except Exception as e:                        # noqa: BLE001
        n_written = 0
        print(f"   ⚠️ ETF份额落库失败: {type(e).__name__} {str(e)[:80]}")
    sh_dates, sh_vals = store.total_shares_series()
    sh_a = align_series(sh_dates, sh_vals, calendar, ALIGN_TOL["shares"])

    # 换手率因子已随"定价权在国内"讨论移出模型(见 gold_config.ATT_WEIGHTS);
    # v2.1: 份额增速也移出温度分(国内资金行为不参与定价), sh_a 仅供速览展示
    # "国内申购热度"(20日增速)与份额库状态

    # ---- 4.5 快讯: 抓取+落库(冷启动型, 历史不可回补) + 时序对齐 ----
    n_news, news_rows, news_ok = update_news(today, asof)
    news_d, news_v = store.news_count_series()
    news_a = align_series(news_d, news_v, calendar, ALIGN_TOL["news"]) \
        if not asof else [None] * len(calendar)

    # ---- 4.6 央行购金(SAFE 月度, 慢变量仅展示不进分数 — framework §6.12) ----
    cb = None
    try:
        cb_d, cb_v = F.fetch_cb_gold_cn()
        cb = G.cb_gold_metrics(cb_d, cb_v.get("gold_wanoz", []),
                               cb_v.get("fx_usd", []), xau_a[idx])
    except Exception as e:                            # noqa: BLE001
        print(f"   ⚠️ 央行购金(SAFE)拉取失败: {type(e).__name__} {str(e)[:80]}")

    # ---- 5. 主价格链与因子计算 ----
    etf_close = align_series(etf_d, etf.get("close", []), calendar, ALIGN_TOL["etf"])
    if st_ok(statuses["sge"]) and sge_a[idx] is not None:
        core, core_src, unit = sge_a, "sge", "¥/克"
    elif st_ok(statuses["etf"]) and etf_d:
        core, core_src, unit = etf_close, etf_src, "元"
    else:
        core, core_src, unit = xau_a, "xau", "USD/盎司"
    if core[idx] is None:
        print("❌ 主价格链最后交易日缺值, 无法计算")
        return None

    series = {"core_close": core, "sge_close": sge_a, "xau_close": xau_a,
              "xag_close": xag_a, "oil_close": oil_a, "usdcny": fx_a,
              "real_rate": rr_a, "m2": m2_a, "cftc_net": cf_a,
              "shares_total": sh_a, "usd_idx": usd_a, "vix": vix_a,
              "dgs10": dgs_a, "news_count": news_a}
    # 国内申购热度(仅展示, 不进分数): 主ETF份额 20日净增速%
    sh_gr = G.pct_returns(sh_a, C.SHARE_GROWTH_DAYS)
    att = G.compute_attention(series, idx)
    temp = G.compute_temperature(series, idx)

    # 时差失真标注: SGE(15:30收盘)不含国际盘隔夜段, 当日伦敦金 |涨跌|≥2% 时
    # 速览里的国内溢价不可解读, 仅展示层标注(溢价已不进分数)
    xau_chg1d = _pct(xau_a[idx], xau_a[idx - 1]) if idx >= 1 else None
    prem_skew = xau_chg1d is not None and abs(xau_chg1d) >= 2.0

    # ---- 6. 组装报告 ----
    prem = G.sge_premium_pct(sge_a[idx], xau_a[idx], fx_a[idx])
    gold_oil = (xau_a[idx] / oil_a[idx]
                if xau_a[idx] is not None and oil_a[idx] not in (None, 0) else None)
    market = {
        "core_source": core_src, "core_unit": unit,
        "core_close": core[idx],
        "core_chg1d": _pct(core[idx], core[idx - 1]) if idx >= 1 else None,
        "core_chg5d": _pct(core[idx], core[idx - 5]) if idx >= 5 else None,
        "xau_chg1d": xau_chg1d,
        "xau_chg5d": _pct(xau_a[idx], xau_a[idx - 5]) if idx >= 5 else None,
        "premium_skew": prem_skew,
        "sge_close": sge_a[idx],
        "xau_close": xau_a[idx],
        "xag_close": xag_a[idx],
        "oil_close": oil_a[idx],
        "gold_oil": gold_oil,
        "share_20d": (sh_gr[idx] * 100
                      if idx < len(sh_gr) and sh_gr[idx] is not None else None),
        "usd_idx": usd_a[idx],
        "vix": vix_a[idx],
        "breakeven": be_a[idx],
        "usdcny": fx_a[idx],
        "premium_pct": prem,
        "premium_quiet": (prem is not None and abs(prem) < C.PREMIUM_NOISE),
        "real_rate": rr_a[idx],
        "cftc_net": cf_a[idx],
        "gap_days": G.days_between(calendar[idx - 1], calendar[idx]) if idx >= 1 else 0,
    }
    rep = {
        "ts": now_ts(),
        "asof": asof or "",
        "report_date": rep_date,
        "calendar_source": cal_src,
        "market": market,
        "attention": _score_block(att, C.ATT_WEIGHTS, "attention"),
        "temperature": _score_block(temp, C.TEMP_WEIGHTS, "temperature", market),
        "coverage": min(att["coverage"], temp["coverage"]),
        "sources": statuses,
        "news": {"count_today": n_news, "sources_ok": news_ok,
                 "rows": news_rows[:8] if not asof else []},
        "cb_gold": cb,
        "shares": {"days": len(sh_dates), "last": sh_dates[-1] if sh_dates else None,
                   "written_today": n_written},
        "_debug": debug,
        "_factors": {"attention": att["factors"], "temperature": temp["factors"]},
    }

    # ---- 6.5 图表数据(仅进HTML, JSON剥离) ----
    n_chart = 500
    cdates = calendar[-n_chart:]

    def _rebase(vals):
        seg = vals[-n_chart:]
        base = next((v for v in seg if v), None)
        if not base:
            return [None] * len(seg)
        return [round(v / base * 100, 2) if v else None for v in seg]

    ma250 = G.ma(core, 250)
    base_core = next((v for v in core[-n_chart:] if v), None)
    prem_full = G.premium_series(sge_a, xau_a, fx_a)
    rep["_chart"] = {
        "dates": cdates,
        "sge100": _rebase(sge_a),
        "etf100": _rebase(etf_close),
        "xau100": _rebase(xau_a),
        "ma250": [round(v / base_core * 100, 2)
                  if v and base_core else None for v in ma250[-n_chart:]],
        "premium": [round(v, 3) if v is not None else None
                    for v in prem_full[-n_chart:]],
        "scores": store.load_scores(min_rows=5),
    }

    # ---- 7. 持久化 + 输出 ----
    try:
        store.upsert_price_snapshot(
            [(rep_date, s, v) for s, v in
             (("sge", sge_a[idx]), ("xau", xau_a[idx]), ("xag", xag_a[idx]),
              ("etf_close", etf_close[idx]))
             if v is not None])
        store.upsert_score(rep_date, rep["attention"]["score"],
                           rep["temperature"]["score"], rep["coverage"],
                           json.dumps({"attention": rep["attention"]["score"],
                                       "temperature": rep["temperature"]["score"],
                                       "coverage": rep["coverage"],
                                       "core": rep["market"]["core_close"]},
                                      ensure_ascii=False))
        store.log_sources(
            [(k, st["ok"], st["n"], st["latest"], st["note"])
             for k, st in statuses.items()])
    except Exception as e:                        # noqa: BLE001
        print(f"⚠️ 本地存档失败: {type(e).__name__} {str(e)[:100]}")

    # ---- 7.5 校准回测(用本次拉齐的序列回放; 不写 score_history) ----
    # 与推送放在 json-only 早退之前: cron 常用 "--json-only --alert" 组合
    if backtest:
        try:
            BT.run_and_print(series, core, calendar)
        except Exception as e:                    # noqa: BLE001
            print(f"⚠️ 回测失败: {type(e).__name__} {str(e)[:120]}")

    # ---- 7.6 推送提醒(历史回看不存在"当下", 禁用) ----
    if alert:
        if asof:
            print("ℹ️ --asof 回看模式不推送")
        else:
            try:
                AL.dispatch(rep, today)
            except Exception as e:                # noqa: BLE001
                print(f"⚠️ 推送失败: {type(e).__name__} {str(e)[:120]}")

    if json_only:
        R.write_json(rep)
        return rep
    R.print_console(rep, debug=debug)
    R.write_json(rep)
    if make_html:
        try:
            path = R.write_html(rep)
            print(f"\n📄 HTML报告: {path}")
        except Exception as e:                    # noqa: BLE001
            print(f"⚠️ HTML生成失败: {type(e).__name__} {str(e)[:100]}")
    return rep


def st_ok(st):
    return bool(st and st.get("ok"))


def _score_block(res, weights, kind, market=None):
    """分数结果 → 报告块(因子行按权重降序, 附元数据与判读)。
    温度分动作文案经 context_action 按近期方向修正(急跌不谈止盈)。"""
    label, icon, action = G.level_of(res["score"], res["levels"])
    if kind == "temperature" and market is not None:
        action = R.context_action(market, label, action)
    rows = []
    for k, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        f = res["factors"].get(k, {})
        meta = C.FACTOR_META.get(k, {})
        rows.append({
            "key": k, "名": meta.get("名", k), "方向": meta.get("方向", ""),
            "窗": meta.get("窗", ""), "组": meta.get("组", ""),
            "weight": w,
            "raw": f.get("raw"), "z": f.get("z"),
            "score": f.get("score"), "note": f.get("note", ""),
        })
    return {"score": res["score"], "coverage": res["coverage"],
            "label": label, "icon": icon, "action": action,
            "missing": res["missing"], "factors": rows, "kind": kind}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="黄金市场雷达: 关注分(今天要不要看) + 温度分(市场什么状态)")
    ap.add_argument("--healthcheck", action="store_true", help="数据源自检")
    ap.add_argument("--no-html", action="store_true", help="不生成HTML报告")
    ap.add_argument("--json-only", action="store_true", help="只写JSON")
    ap.add_argument("--debug", action="store_true", help="打印每因子明细")
    ap.add_argument("--asof", metavar="YYYY-MM-DD", help="历史截面回看")
    ap.add_argument("--backfill-shares", type=int, default=0, metavar="N",
                    help="首次运行回补N个交易日ETF份额(逐日拉取, 较慢)")
    ap.add_argument("--backtest", action="store_true",
                    help="分数→未来5/20日收益 校准回测(约1分钟)")
    ap.add_argument("--alert", action="store_true",
                    help="按规则推送提醒(需 GOLD_SC_SENDKEY/GOLD_PUSHPLUS_TOKEN/"
                         "GOLD_BARK_URL 之一)")
    ap.add_argument("--stats", action="store_true", help="本地数据库状态")
    args = ap.parse_args(argv)

    if args.stats:
        s = store.stats()
        print(f"DB: {s['db_path']}")
        print(f"etf_shares:     {s['shares_rows']} 行 ({s['shares_first']} ~ "
              f"{s['shares_last']}), {s['shares_codes']} 只")
        print(f"score_history:  {s['score_rows']} 行 ({s['score_first']} ~ {s['score_last']})")
        print(f"price_snapshot: {s['price_rows']} 行 ({s['price_first']} ~ {s['price_last']})")
        return 0
    if args.healthcheck:
        return F.healthcheck(date.today().isoformat())

    rep = run(make_html=not args.no_html, json_only=args.json_only,
              debug=args.debug, asof=args.asof,
              backfill_shares=args.backfill_shares,
              backtest=args.backtest, alert=args.alert)
    return 0 if rep else 1


if __name__ == "__main__":
    sys.exit(main())
