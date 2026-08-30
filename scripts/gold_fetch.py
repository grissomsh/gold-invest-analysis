# -*- coding: utf-8 -*-
"""
黄金市场雷达 — 数据获取层(全 skill 唯一 import akshare/requests 的文件)

约定:
  - 每个 fetcher 返回 (dates, payload):
      dates  = ['YYYY-MM-DD', ...] 升序
      payload: 单序列为 [float|None, ...](与 dates 等长);
               多列为 {列名: [float|None, ...]}
  - 任何失败返回 ([], {}) 并带告警, 绝不抛出 — 上游按"该源缺失"降级
  - 每源附 source_status() 做新鲜度检查, 陈旧数据视为当日不可用

数据源(2026-08-29/30 全部实测可用):
  ak.futures_foreign_hist   伦敦金/伦敦银/WTI原油(同一外盘接口, 无成交量)
  ak.spot_hist_sge          上海金交所现货(Au99.99), 约2016年起
  ak.fund_etf_hist_em       国内黄金ETF日行情(含换手率)
  ak.fund_etf_scale_sse     上交所ETF份额(指定单日快照)
  ak.fund_scale_daily_szse  深交所ETF份额(区间)
  ak.bond_zh_us_rate        中/美国债收益率
  FRED fredgraph.csv        DFII10/M2SL/DEXCHUS/DTWEXBGS美元/VIXCLS/DGS10(免key)
  CFTC Socrata API          黄金非商业持仓周报(免key)
  ak.stock_info_global_sina / stock_info_global_cls / futures_news_shmet
                            黄金相关快讯三源(免cookie, 只回最新若干条)
"""

import csv
import io
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import date

import gold_config as C
import gold_scoring as G

try:
    import akshare as ak
    _AK_ERR = None
except Exception as e:                                    # pragma: no cover
    ak = None
    _AK_ERR = e

try:
    import requests
    _REQ_ERR = None
except Exception as e:                                    # pragma: no cover
    requests = None
    _REQ_ERR = e

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def _retry(fn, tries=3, sleep_s=2.0):
    """akshare 接口偶发超时/代理抖动/风控: 失败间隔 sleep 后重试, 最终失败返回 None"""
    last = None
    for k in range(tries):
        try:
            return fn()
        except Exception as e:                            # noqa: BLE001
            last = e
            if k < tries - 1:
                time.sleep(sleep_s)
    print(f"   ⚠️ 拉取失败: {type(last).__name__} {str(last)[:100]}")
    return None


@contextmanager
def no_proxy():
    """临时摘除代理环境变量。东财 push2his 在常见代理下偶发 ProxyError,
    而直连可达; 新浪/FRED/CFTC 不受影响。失败则自动回落(原行为)。"""
    keys = [k for k in list(os.environ) if "proxy" in k.lower()]
    saved = {k: os.environ.pop(k) for k in keys}
    try:
        yield
    finally:
        os.environ.update(saved)


def _retry_noproxy(fn, tries=3, sleep_s=2.0):
    """先带环境原样重试, 全失败后再摘除代理重试一轮(东财直连兜底)"""
    r = _retry(fn, tries=tries, sleep_s=sleep_s)
    if r is None:
        with no_proxy():
            r = _retry(fn, tries=tries, sleep_s=sleep_s)
    return r


def _f(v):
    """单元格 → float|None(容忍 None/NaN/字符串)"""
    try:
        f = float(v)
        return f if f == f else None                     # NaN 判定
    except Exception:
        return None


def _d(v):
    """单元格 → 'YYYY-MM-DD'"""
    s = str(v)[:10]
    return s if len(s) == 10 and s[4] == "-" else None


def _col(df, names):
    """按候选列名取列(容忍 akshare 中英文列名漂移), 缺失返回 None"""
    for n in names:
        if n in getattr(df, "columns", []):
            return df[n]
    return None


def source_status(dates, limit_key, today):
    """新鲜度诊断: {ok, n, latest, stale, note}"""
    limit = C.STALE_LIMITS.get(limit_key, 10)
    n = len(dates)
    latest = dates[-1] if n else None
    stale = G.is_stale(latest, today, limit)
    if n == 0:
        note = "无数据"
    elif stale:
        note = f"陈旧(最后{latest}, 限{limit}日)"
    else:
        note = ""
    return {"ok": n > 0 and not stale, "n": n, "latest": latest,
            "stale": stale, "note": note}


# ------------------------------------------------------------
# akshare 系列
# ------------------------------------------------------------

def fetch_xau(symbol=None):
    """伦敦金现/伦敦银 USD/oz 收盘序列。返回 (dates, closes, status_key)"""
    df = _retry(lambda: ak.futures_foreign_hist(symbol=symbol or C.XAU_SYMBOL))
    if df is None or df.empty:
        return [], {}
    dates = [_d(v) for v in _col(df, ["date", "日期"])]
    closes = [_f(v) for v in _col(df, ["close", "收盘"])]
    pairs = [(d, c) for d, c in zip(dates, closes) if d and c is not None]
    pairs.sort()
    return [p[0] for p in pairs], [p[1] for p in pairs]


def fetch_sge(symbol=None):
    """上海金交所现货收盘(¥/克)"""
    df = _retry(lambda: ak.spot_hist_sge(symbol=symbol or C.SGE_SYMBOL))
    if df is None or df.empty:
        return [], {}
    dates = [_d(v) for v in _col(df, ["date", "日期"])]
    closes = [_f(v) for v in _col(df, ["close", "收盘"])]
    pairs = [(d, c) for d, c in zip(dates, closes) if d and c is not None]
    pairs.sort()
    return [p[0] for p in pairs], [p[1] for p in pairs]


def fetch_etf_daily(code=None, start="20130101"):
    """国内黄金ETF日行情: close 与 换手率(%)。
    热度因子用换手率而非成交额 — 成交额随基金规模增长漂移, 换手率可比。"""
    df = _retry_noproxy(lambda: ak.fund_etf_hist_em(
        symbol=code or C.ETF_PRIMARY, period="daily",
        start_date=start, end_date="20500101", adjust=""))
    if df is None or df.empty:
        return [], {}
    dates = [_d(v) for v in _col(df, ["日期", "date"])]
    close = [_f(v) for v in _col(df, ["收盘", "close"])]
    to = [_f(v) for v in _col(df, ["换手率"])]
    if to is None:                                        # 列漂移兜底: 无换手率则置空
        to = [None] * len(dates)
    pairs = [(d, c, t) for d, c, t in zip(dates, close, to) if d]
    pairs.sort(key=lambda x: x[0])
    return ([p[0] for p in pairs],
            {"close": [p[1] for p in pairs], "turnover": [p[2] for p in pairs]})


def fetch_etf_kline_tencent(code=None, limit=800):
    """腾讯日K兜底(东财不可用时): (dates, closes, volumes_hand)。
    成交量单位为手(×100=份); 换手率需自行计算 vol×100÷流通份额,
    份额来自本地SQLite累积(见 gold_data_store.total_shares_series)。
    腾讯与东财成交量逐手一致, 自算换手率误差<0.01pp。
    注意: 该接口 limit>800 行为异常(静默截断/返回错误结构), 上限锁800,
    覆盖3年滚动分位窗口(750 obs)足够。"""
    code = code or C.ETF_PRIMARY
    tc = ("sh" if code.startswith("5") else "sz") + code

    def _get():
        r = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                         params={"param": f"{tc},day,,,{limit},qfq"},
                         headers=UA, timeout=15)
        r.raise_for_status()
        return r

    r = _retry(_get, tries=2, sleep_s=2.0)
    if r is None:
        return [], [], []
    try:
        node = r.json().get("data", {}).get(tc)
        k = node.get("day") or node.get("qfqday") or [] if isinstance(node, dict) else []
    except Exception:
        return [], [], []
    out = []
    for row in k:
        if len(row) >= 6:
            d = _d(row[0])
            if d:
                out.append((d, _f(row[2]), _f(row[5])))
    out.sort(key=lambda x: x[0])
    return [o[0] for o in out], [o[1] for o in out], [o[2] for o in out]


def fetch_etf_shares_sse(date_compact):
    """上交所全部ETF份额快照(指定交易日, 盘后约19:00发布)。
    返回 {code: shares}(仅黄金ETF池内代码)"""
    df = _retry_noproxy(lambda: ak.fund_etf_scale_sse(date=date_compact))
    if df is None or df.empty:
        return {}
    col_code = _col(df, ["基金代码"])
    col_shares = _col(df, ["基金份额"])
    if col_code is None or col_shares is None:
        return {}
    out = {}
    for c, s in zip(col_code, col_shares):
        c = str(c).strip()
        if c == C.ETF_PRIMARY or c.startswith("518"):
            v = _f(s)
            if v:
                out[c] = v
    return out


def fetch_etf_shares_szse(start, end):
    """深交所ETF份额区间(一次拉取)。返回 {code: shares} 的单日切片不便,
    直接返回 [(date, code, shares)] 由调用方落库"""
    df = _retry_noproxy(lambda: ak.fund_scale_daily_szse(
        start_date=start.replace("-", ""), end_date=end.replace("-", "")))
    if df is None or df.empty:
        return []
    dates = [_d(v) for v in _col(df, ["日期", "date"])]
    codes = _col(df, ["基金代码"])
    shares = _col(df, ["基金份额"])
    if codes is None or shares is None:
        return []
    out = []
    for d, c, s in zip(dates, codes, shares):
        c = str(c).strip()
        if d and c == C.ETF_BACKUP:
            v = _f(s)
            if v:
                out.append((d, c, v))
    return out


def fetch_treasury():
    """中/美国债收益率: {'us10y': [...], 'cn10y': [...]}"""
    df = _retry(lambda: ak.bond_zh_us_rate(start_date="20140101"))
    if df is None or df.empty:
        return [], {}
    dates = [_d(v) for v in _col(df, ["日期", "date"])]
    out = {}
    for key, names in (("us10y", ["美国国债收益率10年"]),
                       ("cn10y", ["中国国债收益率10年"])):
        col = _col(df, names)
        out[key] = [_f(v) for v in col] if col is not None else [None] * len(dates)
    pairs = sorted((d, i) for i, d in enumerate(dates) if d)
    return ([p[0] for p in pairs],
            {k: [v[p[1]] for p in pairs] for k, v in out.items()})


def _norm_ts(v):
    """各源时间 → 'YYYY-MM-DD HH:MM'(sina 带秒/金属网带时区, 统一截断)"""
    s = str(v)[:16]
    return s if len(s) == 16 and s[4] == "-" and s[7] == "-" else None


def _extract_title(text):
    """快讯正文 → 标题: '【标题】正文' 取括号内, 否则取前30字"""
    s = str(text).strip()
    if s.startswith("【"):
        end = s.find("】")
        if 0 < end <= 60:
            return s[1:end]
    return s[:30]


def news_sentiment(text):
    """确定性词典情绪标注(借鉴 gold-agent ±词典法): 多/空/中性。
    只用于展示, 不进分数 — 无时间聚合的情绪均值量纲不可比。"""
    t = str(text).lower()
    b = sum(1 for w in C.NEWS_BULL_WORDS if w in t)
    s = sum(1 for w in C.NEWS_BEAR_WORDS if w in t)
    return "多" if b > s else ("空" if s > b else "中性")


def _news_raw_rows():
    """三源原始行 → ([(ts, 源, 正文)], 成功源列表)。单源失败跳过不抛出。"""
    rows, ok_srcs = [], []

    def add(df, src, tnames, cnames):
        if df is None or df.empty:
            return
        tc, cc = _col(df, tnames), _col(df, cnames)
        if tc is None or cc is None:
            return
        for t, c in zip(tc, cc):
            ts = _norm_ts(t)
            if ts and c:
                rows.append((ts, src, str(c).strip()))
        ok_srcs.append(src)

    add(_retry_noproxy(lambda: ak.stock_info_global_sina()), "新浪",
        ["时间", "发布时间"], ["内容"])
    # 财联社: 日期与时间分列
    df = _retry_noproxy(lambda: ak.stock_info_global_cls())
    if df is not None and not df.empty:
        dc, tc, cc = _col(df, ["发布日期"]), _col(df, ["发布时间"]), _col(df, ["内容"])
        if dc is not None and cc is not None:
            times = ([_norm_ts(f"{d} {t}") for d, t in zip(dc, tc)]
                     if tc is not None else [_d(v) for v in dc])
            for ts, c in zip(times, cc):
                if ts and c:
                    rows.append((ts, "财联社", str(c).strip()))
            ok_srcs.append("财联社")
    add(_retry_noproxy(lambda: ak.futures_news_shmet()), "金属网",
        ["发布时间"], ["内容"])
    return rows, ok_srcs


def fetch_news(max_age_days=3, today=None):
    """黄金相关快讯(三源合并→标题去重→关键词过滤→情绪标注)。
    返回 (rows, 成功源数): rows=[{"时间","源","标题","情绪","内容"}] 按时间降序,
    只保留近 max_age_days 天; 成功源数=0 表示三源均失败(与"无命中"区分,
    调用方不应把失败当 0 落库)。
    关键词与情绪只匹配**标题** — 正文误命中率高(实测"黄金期""蜂蜜"均来自
    迎宾/时政正文)。打分用"当日条数"由调用方按 rep_date 统计;
    历史无法回补(各源只回最新若干条)。"""
    if ak is None:
        return [], 0
    rows, ok_srcs = _news_raw_rows()
    ref = date.fromisoformat(today) if today else date.today()
    cutoff_str = ref.fromordinal(ref.toordinal() - max_age_days + 1).isoformat()
    seen, out = set(), []
    for ts, src, text in sorted(rows, key=lambda r: r[0], reverse=True):
        if ts[:10] < cutoff_str:
            continue
        title = _extract_title(text)
        key = title[:24]
        if key in seen or not any(k in title for k in C.NEWS_KEYWORDS):
            continue
        seen.add(key)
        out.append({"时间": ts, "源": src, "标题": title,
                    "情绪": news_sentiment(title), "内容": text[:120]})
    return out, len(ok_srcs)


# ------------------------------------------------------------
# 免 key HTTP 系列 (FRED / CFTC)
# ------------------------------------------------------------

def fetch_fred(series_id):
    """FRED CSV(免key)。'.' 为缺失值。返回 (dates, values)"""
    if requests is None:
        return [], []
    def _get():
        r = requests.get(C.FRED_BASE.format(sid=series_id),
                         headers=UA, timeout=20)
        r.raise_for_status()
        return r
    r = _retry(_get, tries=2, sleep_s=2.0)
    if r is None:
        return [], []
    rows = list(csv.reader(io.StringIO(r.text)))
    out_d, out_v = [], []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        d = _d(row[0])
        v = None if row[1].strip() in (".", "") else _f(row[1])
        if d:
            out_d.append(d)
            out_v.append(v)
    order = sorted(range(len(out_d)), key=lambda i: out_d[i])
    return [out_d[i] for i in order], [out_v[i] for i in order]


def fetch_cb_gold_cn():
    """中国央行黄金储备(SAFE国家外汇管理局, 东方财富数据中心)。
    月频(官方滞后约2-4周)。返回 (['YYYY-MM',...], {'gold_wanoz':[万盎司], 'fx_usd':[亿美元]})"""
    df = _retry_noproxy(lambda: ak.macro_china_foreign_exchange_gold())
    if df is None or df.empty:
        return [], {}
    raw_d = _col(df, ["统计时间"])
    g = _col(df, ["黄金储备"])
    fx = _col(df, ["国家外汇储备"])
    if raw_d is None or g is None:
        return [], {}
    out = []
    for d, gv, fv in zip(raw_d, g, fx if fx is not None else [None] * len(raw_d)):
        m = re.match(r"^(\d{4})[.\-/](\d{1,2})$", str(d).strip())
        if m and _f(gv) is not None:
            out.append((f"{m.group(1)}-{int(m.group(2)):02d}", _f(gv), _f(fv)))
    out.sort()
    return ([o[0] for o in out],
            {"gold_wanoz": [o[1] for o in out], "fx_usd": [o[2] for o in out]})


def fetch_cftc_gold():
    """CFTC 黄金非商业净多头(周频): net = long_all − short_all"""
    if requests is None:
        return [], []
    def _get():
        r = requests.get(C.CFTC_URL, headers=UA, timeout=30, params={
            "$where": C.CFTC_GOLD_FILTER,
            "$select": "report_date_as_yyyy_mm_dd,"
                       "noncomm_positions_long_all,noncomm_positions_short_all",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": "5000"})
        r.raise_for_status()
        return r
    r = _retry(_get, tries=2, sleep_s=3.0)
    if r is None:
        return [], []
    try:
        rows = json.loads(r.text)
    except Exception:
        return [], []
    out = []
    for row in rows:
        d = _d(str(row.get("report_date_as_yyyy_mm_dd", ""))[:10])
        lo = _f(row.get("noncomm_positions_long_all"))
        sh = _f(row.get("noncomm_positions_short_all"))
        if d and lo is not None and sh is not None:
            out.append((d, lo - sh))
    out.sort()
    return [o[0] for o in out], [o[1] for o in out]


# ------------------------------------------------------------
# 环境自检
# ------------------------------------------------------------

def healthcheck(today):
    """逐源探测, 打印诊断; 返回退出码(0/1)。"""
    state = {"ok": True}

    def step(name, key, fn):
        try:
            dates, payload = fn()
            st = source_status(dates, key, today)
            n = len(payload) if isinstance(payload, dict) else 1
            mark = "✅" if st["ok"] else "⚠️ "
            if not st["ok"]:
                state["ok"] = False
            print(f"{mark} {name}: {st['n']}行, 最后 {st['latest']} {st['note']}")
            return dates, payload
        except Exception as e:                            # noqa: BLE001
            state["ok"] = False
            print(f"❌ {name}: {type(e).__name__} {str(e)[:100]}")
            return [], {}

    if ak is None:
        print(f"❌ akshare 导入失败: {_AK_ERR}")
        return 1
    if requests is None:
        print(f"❌ requests 导入失败: {_REQ_ERR}")
        return 1

    d, _ = step("上海金 Au99.99", "sge", fetch_sge)
    step("伦敦金 XAU", "xau", fetch_xau)
    step("伦敦银 XAG", "gold_silver", lambda: fetch_xau(C.XAG_SYMBOL))
    step("WTI原油 CL", "oil", lambda: fetch_xau(C.OIL_SYMBOL))
    step("黄金ETF行情", "etf",
         lambda: fetch_etf_daily(C.ETF_PRIMARY))
    step("中/美国债收益率", "treasury", fetch_treasury)
    step("FRED 实际利率 DFII10", "real_rate", lambda: fetch_fred(C.FRED_SERIES["real_rate"]))
    step("FRED 美国M2 M2SL", "m2", lambda: fetch_fred(C.FRED_SERIES["m2"]))
    step("FRED 汇率 DEXCHUS", "usdcny", lambda: fetch_fred(C.FRED_SERIES["usdcny"]))
    step("FRED 美元指数 DTWEXBGS", "usd_idx", lambda: fetch_fred(C.FRED_SERIES["usd_idx"]))
    step("FRED VIX VIXCLS", "vix", lambda: fetch_fred(C.FRED_SERIES["vix"]))
    step("FRED 美债10Y DGS10", "dgs10", lambda: fetch_fred(C.FRED_SERIES["dgs10"]))
    step("CFTC 黄金非商业净持仓", "cftc", fetch_cftc_gold)

    # 快讯源只回最新若干条, 无"新鲜度"概念 — 失败仅降级(news_heat 权重重归一),
    # 不影响整体退出码
    try:
        nr, n_ok = fetch_news(max_age_days=3)
        if nr:
            srcs = sorted({r["源"] for r in nr})
            print(f"✅ 黄金快讯: 近3日 {len(nr)} 条 (源: {', '.join(srcs)})")
        else:
            print(f"⚠️  黄金快讯: 近3日无匹配条目(成功源 {n_ok}/3)")
    except Exception as e:                                # noqa: BLE001
        print(f"⚠️  黄金快讯: {type(e).__name__} {str(e)[:100]}")

    def _sse():
        ref = d[-1] if d else None
        if not ref:
            raise RuntimeError("无交易日参照")
        got = fetch_etf_shares_sse(ref.replace("-", ""))
        if not got:
            raise RuntimeError("当日无份额(盘后19:00前属正常)")
        return f"{ref} {len(got)}只黄金ETF"

    try:
        print(f"✅ 上交所ETF份额: {_sse()}")
    except Exception as e:                                # noqa: BLE001
        state["ok"] = False
        print(f"❌ 上交所ETF份额: {e}")

    return 0 if state["ok"] else 1
