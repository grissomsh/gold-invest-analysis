# -*- coding: utf-8 -*-
"""
黄金市场雷达 — 输出层: 控制台表格 / HTML报告 / JSON

HTML 为单文件(内联CSS + ECharts, 三CDN兜底), 视觉规范取自 dataviz 方法:
  - 类目色固定顺序 蓝#2a78d6 / 橙#eb6834 / 青#1baf7a(已过CVD验证; 青色低对比
    → 图内直接标签+表格视图救济)
  - 状态色仅用于判读档位(过热critical/偏热serious/中性muted/偏冷蓝), 永远
    "色点+文字标签"成对出现, 不让颜色单独承载语义
  - 文字一律墨色 token, 不穿系列色; 网格细线退后
"""

import json
import os

import gold_config as C

HTML_PATH = os.path.join(C.WORKSPACE, "黄金市场雷达.html")
JSON_PATH = os.path.join(C.WORKSPACE, "黄金市场雷达.json")

# 原始值按百分数格式化的因子(打分层已 ×100)
PCT_KEYS = {"ret1d_z", "ret5d_z", "etf_turnover", "premium_z", "vol_spike",
            "ma250_dev", "mom20", "mom60", "mom120", "share_growth",
            "rv20_pct", "real_rate", "ath_prox"}


# ============================================================
# 格式化
# ============================================================

def _fmt(v, nd=2, suffix=""):
    if v is None:
        return "—"
    try:
        return f"{v:,.{nd}f}{suffix}"
    except Exception:
        return str(v)


def _fmt_pct(v, nd=2):
    if v is None:
        return "—"
    return f"{v:+.{nd}f}%"


def _raw(key, v):
    """因子原始值 → 展示串(按量纲选择格式)"""
    if v is None:
        return "—"
    if key == "gold_m2":
        return _fmt(v, 5)
    if key == "gold_silver":
        return _fmt(v, 1)
    if key == "gold_oil":
        return _fmt(v, 1)
    if key == "cftc_net":
        return _fmt(v, 0)
    if key in ("usd_idx", "vix"):
        return _fmt(v, 1)
    return _fmt(v, 2, "%")


def _pad(s, width):
    """CJK感知补空格(>127 视为宽字符)"""
    disp = 0
    out = ""
    for ch in str(s):
        out += ch
        disp += 2 if ord(ch) > 127 else 1
    return out + " " * max(0, width - disp)


def _cov_label(cov):
    if cov is None:
        return "—"
    if cov >= C.COVERAGE_HIGH:
        return "高"
    if cov >= C.COVERAGE_MID:
        return "中"
    return "低"


# 分档色带(判读档位 → 标尺底色)。温度=冷→热光谱, 关注=严重度渐进;
# 颜色仅作位置辅助, 语义仍由"色点+文字"的档位 chip 承载
BAND_COLORS = {
    "日常波动": "#eae9e3", "值得看一眼": "#f6c65b", "重要异动": "#d03b3b",
    "过冷": "#cde2fb", "偏冷": "#86b6ef", "中性": "#f0efec",
    "偏热": "#ec835a", "过热": "#d03b3b",
}


def _strip_html(levels, score):
    """判读分档 → 卡片内嵌单条带状标尺(替代原独立温度仪表盘)。
    levels 为降序 (下限,标签,灯,提示); 输出色带 + 分档刻度 + 当前值指针。"""
    lv = sorted(levels, key=lambda x: x[0])
    segs = []
    for i, (floor, label, _ic, _ac) in enumerate(lv):
        hi = lv[i + 1][0] if i + 1 < len(lv) else 100
        color = BAND_COLORS.get(label, "#e1e0d9")
        segs.append(f'<div class="seg" style="width:{hi - floor}%;'
                    f'background:{color}"></div>')
    ticks = "".join(f'<span class="tick" style="left:{lv[i + 1][0]}%">'
                    f'{lv[i + 1][0]}</span>'
                    for i in range(len(lv) - 1))
    mark = (f'<div class="mark" style="left:calc({score}% - 2px)"></div>'
            if score is not None else "")
    return (f'<div class="strip"><div class="fill">{"".join(segs)}</div>'
            f'{mark}{ticks}</div>')


# ============================================================
# 一句话结论
# ============================================================

def context_action(market, label, base_action):
    """按近期价格方向修正档位动作文案。

    档位默认动作(TemPERATURE_LEVELS/ATTENTION_LEVELS 的第三列)描述的是
    "该档的典型场景"; 急跌/急涨时照搬会误导(如刚急跌就谈止盈)。
    规则: 任一市场(主链/伦敦金)单日涨跌 ≤-2% 视为急跌, ≥+2% 视为急涨。
    """
    cands = [v for v in (market.get("core_chg1d"), market.get("xau_chg1d"))
             if v is not None]
    if not cands:
        return base_action
    hot = label in ("过热", "偏热")
    cold = label in ("过冷", "偏冷")
    if min(cands) <= -2.0:
        # 急跌日不追卖(卖在恐慌价最差); 但减仓条件用"反弹收不回失地",
        # 利用反弹分批卖 — 绝不等"跌破前低"(回吐过大)。方向未确认前不抄底。
        if hot:
            return ("急跌方向未确认：不追卖、不抄底；快速收复失地=洗盘(持有观察)，"
                    "收不回失地=反弹结束信号，利用反弹分批减仓，勿等跌破前低")
        if cold:
            return "急跌逼近左侧区但方向未确认：抄底仅小步分批，不加速"
        return "急跌观望：不追卖不抄底；反弹能否收复失地是方向判据"
    if max(cands) >= 2.0:
        if hot:
            return "亢奋上涨：持有，预设计划性减仓纪律（涨势中分批锁定），不追买"
        if cold:
            return "反弹修复中：维持左侧小步节奏，不因急涨加速"
        return "急涨不加仓，持有观察"
    return base_action


# ------------------------------------------------------------
# 多空速览 — gold-agent 辩论结构的确定性压缩(展示层, 非分数)
# 高分因子对金价的方向含义: +1=偏多证据, -1=偏空/风险证据, 0=方向中性事件。
# 注意与 FACTOR_META 的"方向"(冷热语义)不同: 冷因子(反向)高分=压制项处于
# 低位, 本身就是利多证据(如实际利率分位高=利率低=机会成本低)。
# ------------------------------------------------------------
KEY_BIAS = {
    "mom20": 1, "mom60": 1, "mom120": 1, "ma250_dev": 1,     # 趋势向上
    "ath_prox": 1,                                           # 逼近新高=强势
    "real_rate": 1, "usd_idx": 1,                            # 机会成本/美元压制弱=利多
    "gold_silver": 1, "gold_oil": 1,                         # 比价低=贵金属端不弱
    "vix": 1, "breakeven": 1, "share_growth": 1, "cftc_net": 1,
    # (避险/抗通胀/申购/投机多头需求; CFTC 拥挤的双面性在框架文档注明)
    "news_heat": 0, "ret1d_z": 0, "ret5d_z": 0, "vol_spike": 0,
    "rv20_pct": -1,                                          # 高波动=追加风险高
    "gold_m2": -1,                                           # 估值贵=风险证据
}


def bull_bear(rep):
    """多空速览: (结论三态, 偏多证据[:3], 偏空证据[:3])。
    每条证据带原始值与分数; 双侧证据数差≥2 且≥2条才给方向, 否则判拉锯
    (对应仲裁官'不确定时不要强行选边')。"""
    bull, bear = [], []
    for blk in (rep["attention"], rep["temperature"]):
        for f in blk["factors"]:
            if f["score"] is None or f["score"] < 60:
                continue
            b = KEY_BIAS.get(f["key"], 0)
            item = f"{f['名']} {_raw(f['key'], f['raw'])} ({f['score']:.0f})"
            if b > 0:
                bull.append(item)
            elif b < 0:
                bear.append(item)
    verdict = "拉锯"
    if len(bull) >= 2 and len(bull) >= len(bear) + 2:
        verdict = "偏多"
    elif len(bear) >= 2 and len(bear) >= len(bull) + 2:
        verdict = "偏空"
    return verdict, bull[:3], bear[:3]


def one_liner(rep):
    """确定性模板: 点名最强异动因子(带z) + 市场状态 + 节假日累计提示"""
    m = rep["market"]
    att, temp = rep["attention"], rep["temperature"]
    parts = []

    top = sorted([f for f in att["factors"] if f["score"] is not None],
                 key=lambda f: -(f["score"] or 0))
    for f in top[:2]:
        if f["score"] < 55:
            break
        z = f" (z={f['z']:+.1f})" if f.get("z") is not None else ""
        parts.append(f"{f['名']} {_raw(f['key'], f['raw'])}{z}")
    if not parts:
        parts.append("各因子无异动")

    line = "；".join(parts)
    gap = m.get("gap_days") or 0
    if gap >= 3:
        line += f"（距上一交易日{gap}天, 为累计值）"
    if temp["label"]:
        line += f"。市场{temp['label']}{temp['icon']}"
        if m.get("premium_pct") is not None and not m.get("premium_quiet"):
            line += f", 国内溢价{_fmt_pct(m['premium_pct'], 1)}"
    line += f"。{temp['action']}" if temp.get("action") else ""
    return line


# ============================================================
# 控制台
# ============================================================

def _print_temp_table(rows, missing):
    print("\n===== 温度分(市场状态 0-100, 越高越热) =====")
    if missing:
        print(f"   缺失因子: {', '.join(missing)}")
    hdr = ["组别", "因子", "原始值", "分数", "权重", "方向", "窗口"]
    w = [6, 15, 10, 6, 6, 6, 8]
    print(" ".join(_pad(h, x) for h, x in zip(hdr, w)))
    for r in rows:
        cells = [r["组"] or "—", r["名"], _raw(r["key"], r["raw"]),
                 _fmt(r["score"], 1), f"{r['weight']}%", r["方向"], r["窗"]]
        print(" ".join(_pad(c, x) for c, x in zip(cells, w)))


def _print_att_table(rows):
    print("\n===== 关注分(今日异动 0-100, 越高越该看) =====")
    hdr = ["因子", "原始值", "z", "分数", "权重", "窗口"]
    w = [15, 11, 7, 6, 6, 8]
    print(" ".join(_pad(h, x) for h, x in zip(hdr, w)))
    for r in rows:
        z = f"{r['z']:+.2f}" if r.get("z") is not None else "—"
        cells = [r["名"], _raw(r["key"], r["raw"]), z,
                 _fmt(r["score"], 1), f"{r['weight']}%", r["窗"]]
        print(" ".join(_pad(c, x) for c, x in zip(cells, w)))


def _print_news(news):
    rows = news.get("rows") or []
    print("\n----- 黄金相关快讯(近3日, 情绪为词典标注仅供参考) -----")
    if news.get("count_today") is None:
        print("   快讯源当日不可得(或 --asof 回看) — news_heat 因子本期缺失")
    else:
        print(f"   今日命中 {news['count_today']} 条 (成功源 {news.get('sources_ok', 0)}/3)")
    for r in rows[:6]:
        print(f"   {r['时间'][5:16]} [{r['源']}·{r['情绪']}] {r['标题'][:40]}")


def _print_bull_bear(rep):
    verdict, bull, bear = bull_bear(rep)
    print(f"\n----- 多空速览(因子高分方向解读, 展示非预测) -----")
    print(f"结论: {verdict}")
    print(f"{'偏多证据':<8} " + ("；".join(bull) if bull else "证据不足"))
    print(f"{'偏空/风险':<7} " + ("；".join(bear) if bear else "证据不足"))


def print_console(rep, debug=False):
    m = rep["market"]
    att, temp = rep["attention"], rep["temperature"]
    bar = "=" * 72
    print(bar)
    asof_tag = f"（asof {rep['asof']}）" if rep.get("asof") else ""
    print(f"🥇 黄金市场雷达  {rep['ts']}   数据截至 {rep['report_date']}{asof_tag}")
    print(bar)
    print(f"关注分 {_fmt(att['score'],1)} {att['label']}{att['icon']}   |   "
          f"温度分 {_fmt(temp['score'],1)} {temp['label']}{temp['icon']}   |   "
          f"覆盖度 {_fmt(rep['coverage']*100,0)}% ({_cov_label(rep['coverage'])})")
    print(f"一句话: {one_liner(rep)}")

    print("\n----- 市场速览 -----")
    core_name = {"sge": "上海金", "xau": "伦敦金"}.get(m["core_source"], f"黄金ETF({m['core_source']})")
    print(f"{_pad(core_name, 10)} {_fmt(m['core_close'])} {m['core_unit']}   "
          f"日{_fmt_pct(m['core_chg1d'])}   5日{_fmt_pct(m['core_chg5d'])}")
    if m["core_source"] != "sge":
        print(f"{_pad('上海金', 10)} {_fmt(m['sge_close'])} ¥/克")
    print(f"{_pad('伦敦金', 10)} {_fmt(m['xau_close'])} USD/oz   "
          f"日{_fmt_pct(m.get('xau_chg1d'))}  5日{_fmt_pct(m.get('xau_chg5d'))}   "
          f"USDCNY {_fmt(m['usdcny'])}")
    prem = m.get("premium_pct")
    prem_tag = ""
    if m.get("premium_quiet"):
        prem_tag = "（汇率基差噪声内）"
    elif m.get("premium_skew"):
        prem_tag = "（含时差失真, 今日不可解读）"
    print(f"{_pad('国内溢价', 10)} {_fmt_pct(prem)} {prem_tag}")
    print(f"{_pad('实际利率', 10)} {_fmt(m['real_rate'])}%      "
          f"{_pad('CFTC净多', 6)} {_fmt(m['cftc_net'], 0)} 手")
    print(f"{_pad('美元指数', 10)} {_fmt(m.get('usd_idx'), 1)}       "
          f"{_pad('VIX', 6)} {_fmt(m.get('vix'), 1)}")
    print(f"{_pad('金油比', 10)} {_fmt(m.get('gold_oil'), 1)}"
          f" (WTI {_fmt(m.get('oil_close'), 1)})   "
          f"{_pad('通胀预期', 6)} {_fmt(m.get('breakeven'), 2)}%")
    sh = rep.get("shares", {})
    print(f"{_pad('ETF份额库', 10)} {sh.get('days', 0)} 日 (至 {sh.get('last') or '—'})"
          f"   今日写入 {sh.get('written_today', 0)} 条")

    _print_temp_table(temp["factors"], temp["missing"])
    _print_att_table(att["factors"])
    _print_news(rep.get("news", {}))
    _print_bull_bear(rep)

    if debug:
        print("\n----- 明细(debug) -----")
        for kind, blk in (("温度", temp), ("关注", att)):
            for f in blk["factors"]:
                print(f"[{kind}] {f['key']:<14} raw={f['raw']} z={f['z']} "
                      f"score={f['score']} w={f['weight']} {f['note'] or 'ok'}"
                      if f["score"] is None else
                      f"[{kind}] {f['key']:<14} raw={f['raw']} z={f['z']} "
                      f"score={f['score']} w={f['weight']}")
    print("\n温度分为市场状态描述, 不构成投资建议。方法论: references/gold_framework.md")


# ============================================================
# JSON
# ============================================================

def write_json(rep):
    os.makedirs(C.WORKSPACE, exist_ok=True)
    slim = {k: v for k, v in rep.items() if not k.startswith("_")}
    with open(JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(slim, fh, ensure_ascii=False, indent=1, default=str)
    return JSON_PATH


# ============================================================
# HTML
# ============================================================

def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _rows_html(rows, kind):
    """因子表 HTML 行"""
    out = []
    for r in rows:
        if kind == "temp":
            cells = [r["组"] or "—", _esc(r["名"]), _raw(r["key"], r["raw"]),
                     _fmt(r["score"], 1), f"{r['weight']}%", r["方向"], r["窗"]]
        else:
            z = f"{r['z']:+.2f}" if r.get("z") is not None else "—"
            cells = [_esc(r["名"]), _raw(r["key"], r["raw"]), z,
                     _fmt(r["score"], 1), f"{r['weight']}%", r["窗"]]
        cls = ' class="miss"' if r["score"] is None else ""
        tds = "".join(f"<td>{c}</td>" for c in cells)
        out.append(f"<tr{cls}>{tds}</tr>")
    return "\n".join(out)


def _sources_html(rep):
    order = ["sge", "xau", "xag", "oil", "etf", "treasury", "real_rate", "m2",
             "usdcny", "usd_idx", "vix", "dgs10", "cftc", "shares"]
    names = {"sge": "上海金 Au99.99", "xau": "伦敦金 XAU", "xag": "伦敦银 XAG",
             "oil": "WTI原油 CL", "etf": "黄金ETF行情", "treasury": "国债收益率",
             "real_rate": "实际利率 DFII10", "m2": "美国M2 M2SL",
             "usdcny": "USDCNY DEXCHUS", "usd_idx": "美元指数 DTWEXBGS",
             "vix": "VIX VIXCLS", "dgs10": "美债10Y DGS10",
             "cftc": "CFTC净持仓", "shares": "ETF份额(本地库)"}
    out = []
    for k in order:
        if k == "shares":
            sh = rep.get("shares", {})
            out.append(f'<tr><td><span class="dot ok"></span>{names[k]}</td>'
                       f"<td>—</td><td>{sh.get('last') or '—'}</td>"
                       f"<td>{sh.get('days', 0)} 日累积</td></tr>")
            continue
        st = rep.get("sources", {}).get(k)
        if not st:
            continue
        dot = "ok" if st.get("ok") else "warn"
        out.append(f'<tr><td><span class="dot {dot}"></span>{names[k]}</td>'
                   f"<td>{st.get('n', '—')}</td>"
                   f"<td>{st.get('latest') or '—'}</td>"
                   f"<td>{_esc(st.get('note') or '正常')}</td></tr>")
    return "\n".join(out)


def write_html(rep):
    os.makedirs(C.WORKSPACE, exist_ok=True)
    m, att, temp = rep["market"], rep["attention"], rep["temperature"]
    chart = rep.get("_chart", {})
    scores = chart.get("scores") or []
    grp_code = {"估值": 100, "趋势": 200, "拥挤": 300, "宏观": 400}
    verdict, bull, bear = bull_bear(rep)
    news = rep.get("news", {})
    news_lis = "".join(
        f'<li><span class="nt">{r["时间"][5:16]}</span>'
        f'<span class="ns">{r["源"]}·{r["情绪"]}</span>{_esc(r["标题"])}</li>'
        for r in (news.get("rows") or [])[:6]) or '<li class="nmuted">近3日无命中</li>'
    bb = lambda items: ("；".join(_esc(i) for i in items) if items else "证据不足")

    lvl_cls = {"过热": "crit", "偏热": "ser", "中性": "mid",
               "偏冷": "cool", "过冷": "cold", "重要异动": "crit",
               "值得看一眼": "warn", "日常波动": "mid"}

    tokens = {
        "@TS@": _esc(rep["ts"]),
        "@DATE@": _esc(rep["report_date"]),
        "@ATT_SCORE@": _fmt(att["score"], 1),
        "@ATT_LABEL@": _esc(att["label"]),
        "@ATT_ICON@": att["icon"],
        "@ATT_CLS@": lvl_cls.get(att["label"], "mid"),
        "@ATT_ACTION@": _esc(att["action"]),
        "@TEMP_SCORE@": _fmt(temp["score"], 1),
        "@TEMP_LABEL@": _esc(temp["label"]),
        "@TEMP_ICON@": temp["icon"],
        "@TEMP_CLS@": lvl_cls.get(temp["label"], "mid"),
        "@TEMP_ACTION@": _esc(temp["action"]),
        "@COV@": f"{rep['coverage']*100:.0f}%（{_cov_label(rep['coverage'])}）",
        "@ONE_LINER@": _esc(one_liner(rep)),
        "@ATT_STRIP@": _strip_html(C.ATTENTION_LEVELS, att["score"]),
        "@TEMP_STRIP@": _strip_html(C.TEMPERATURE_LEVELS, temp["score"]),
        "@TEMP_ROWS@": _rows_html(temp["factors"], "temp"),
        "@TEMP_MISSING@": ("缺失: " + _esc(", ".join(temp["missing"])))
        if temp["missing"] else "",
        "@ATT_ROWS@": _rows_html(att["factors"], "att"),
        "@SRC_ROWS@": _sources_html(rep),
        "@CORE_NAME@": "上海金" if m["core_source"] == "sge" else
                       ("伦敦金" if m["core_source"] == "xau" else "黄金ETF"),
        "@CORE_VAL@": _fmt(m["core_close"]),
        "@CORE_UNIT@": _esc(m["core_unit"]),
        "@CORE_CHG@": _fmt_pct(m["core_chg1d"]),
        "@CORE_CHG5@": _fmt_pct(m["core_chg5d"]),
        "@XAU_VAL@": _fmt(m["xau_close"]),
        "@XAU_CHG@": _fmt_pct(m.get("xau_chg1d")),
        "@XAU_CHG5@": _fmt_pct(m.get("xau_chg5d")),
        "@FX@": _fmt(m["usdcny"]),
        "@PREM@": _fmt_pct(m["premium_pct"]),
        "@PREM_TAG@": ("汇率基差噪声内" if m.get("premium_quiet")
                       else ("上海金 vs 伦敦金×汇率 · 含时差失真, 今日不可解读"
                             if m.get("premium_skew")
                             else "上海金 vs 伦敦金×汇率")),
        "@RR@": _fmt(m["real_rate"]),
        "@USD@": _fmt(m.get("usd_idx"), 1),
        "@VIX@": _fmt(m.get("vix"), 1),
        "@GO@": _fmt(m.get("gold_oil"), 1),
        "@BE@": _fmt(m.get("breakeven"), 2),
        "@CFTC@": _fmt(m["cftc_net"], 0),
        "@NEWS_TAG@": ("今日命中 %d 条 (成功源 %d/3)"
                       % (news["count_today"], news.get("sources_ok", 0))
                       if news.get("count_today") is not None
                       else "快讯源当日不可得 — news_heat 本期缺失"),
        "@NEWS_LIS@": news_lis,
        "@BB_VERDICT@": verdict,
        "@BB_CLS@": {"偏多": "cool", "偏空": "ser"}.get(verdict, "mid"),
        "@BB_BULL@": bb(bull),
        "@BB_BEAR@": bb(bear),
        "@NOISE@": _fmt(C.PREMIUM_NOISE, 1),
        "@SCORES_PLACEHOLDER@": "" if len(scores) >= 5 else
        '<div class="ph">分数历史需本地逐日积累（≥5个运行日后可画曲线）</div>',
        "@ATT_DATA@": json.dumps([[f["名"], f["score"]] for f in att["factors"]],
                                 ensure_ascii=False),
        "@TEMP_DATA@": json.dumps([[f["名"], f["score"], grp_code.get(f["组"], 0),
                                    f["方向"]] for f in temp["factors"]],
                                  ensure_ascii=False),
    }

    html = _HTML_TEMPLATE
    for k, v in tokens.items():
        html = html.replace(k, v)
    payload = json.dumps({
        "dates": chart.get("dates") or [],
        "sge100": chart.get("sge100") or [],
        "etf100": chart.get("etf100") or [],
        "xau100": chart.get("xau100") or [],
        "ma250": chart.get("ma250") or [],
        "premium": chart.get("premium") or [],
        "premium_noise": C.PREMIUM_NOISE,
        "scores": [[r[0], r[1], r[2]] for r in scores],
    }, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("@CHART_DATA@", payload)
    with open(HTML_PATH, "w", encoding="utf-8") as fh:
        fh.write(html)
    return HTML_PATH


_HTML_TEMPLATE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>黄金市场雷达</title>
<style>
:root{color-scheme:light;
 --page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--baseline:#c3c2b7;--border:rgba(11,11,11,.10);
 --c1:#2a78d6;--c2:#eb6834;--c3:#1baf7a;
 --good:#0ca30c;--warn:#fab219;--ser:#ec835a;--crit:#d03b3b;
 --cool:#5598e7;--cold:#1c5cab;}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
 font:14px/1.6 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:24px 20px 48px}
header{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}
h1{font-size:20px;margin:0}
.meta{color:var(--muted);font-size:12px}
.hero{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:18px 0 8px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px}
.tile .k{font-size:13px;color:var(--ink2)}
.tile .v{font-size:44px;font-weight:700;line-height:1.15}
.tile .lbl{font-size:15px;font-weight:600;margin-top:2px}
.tile .act{font-size:12.5px;color:var(--ink2);margin-top:14px}
.strip{position:relative;height:10px;border-radius:99px;margin-top:12px}
.strip .fill{display:flex;width:100%;height:100%;border-radius:99px;overflow:hidden;border:1px solid var(--border)}
.strip .seg{height:100%}
.strip .mark{position:absolute;top:-3px;width:4px;height:16px;border-radius:2px;background:var(--ink);box-shadow:0 0 0 2px var(--surface)}
.strip .tick{position:absolute;top:11px;font-size:10px;line-height:1.4;color:var(--muted);transform:translateX(-50%)}
.chip{display:inline-block;padding:1px 10px;border-radius:99px;font-size:13px;font-weight:600;
 border:1px solid var(--border);background:var(--page)}
.chip.crit{color:var(--crit)}.chip.ser{color:var(--ser)}.chip.warn{color:#8a6200}
.chip.mid{color:var(--ink2)}.chip.cool{color:var(--cool)}.chip.cold{color:var(--cold)}
.oneliner{background:var(--surface);border:1px solid var(--border);border-radius:10px;
 padding:10px 14px;margin:10px 0 18px;color:var(--ink)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin-top:14px}
.card h3{margin:0 0 6px;font-size:14px;font-weight:600}
.card .sub{font-size:12px;color:var(--muted);margin-bottom:4px}
.chart{width:100%;height:300px}
.chart.tall{height:340px}.chart.sq{height:260px}
.mkt{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.mkt .cell{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 12px}
.mkt .k{font-size:12px;color:var(--muted)}
.mkt .v{font-size:17px;font-weight:600;font-variant-numeric:tabular-nums}
.mkt .s{font-size:12px;color:var(--ink2)}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:4px}
th,td{padding:5px 8px;border-bottom:1px solid var(--grid);text-align:left;font-variant-numeric:tabular-nums}
th{color:var(--muted);font-weight:500;font-size:12px}
tr.miss td{color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px}
.dot.ok{background:var(--good)}.dot.warn{background:var(--warn)}
.legend{display:flex;gap:16px;font-size:12px;color:var(--ink2);margin:2px 0 4px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
ul.news{list-style:none;margin:6px 0 0;padding:0;font-size:13px}
ul.news li{padding:5px 0;border-bottom:1px solid var(--grid);line-height:1.5}
ul.news li:last-child{border-bottom:none}
ul.news .nt{color:var(--muted);font-size:12px;margin-right:8px;font-variant-numeric:tabular-nums}
ul.news .ns{display:inline-block;color:var(--ink2);font-size:12px;margin-right:8px}
ul.news .nmuted{color:var(--muted)}
.ph{color:var(--muted);font-size:13px;padding:24px 0;text-align:center}
footer{margin-top:26px;color:var(--muted);font-size:12px;line-height:1.8}
@media (max-width:860px){.hero{grid-template-columns:1fr}.grid2{grid-template-columns:1fr}}
</style></head><body><div class="wrap">
<header>
 <h1>🥇 黄金市场雷达</h1>
 <div class="meta">数据截至 @DATE@ · 生成于 @TS@ · 覆盖度 @COV@</div>
</header>

<div class="hero">
 <div class="tile">
  <div class="k">关注分 · 今天要不要看</div>
  <div class="v">@ATT_SCORE@</div>
  <div class="lbl"><span class="chip @ATT_CLS@">@ATT_ICON@ @ATT_LABEL@</span></div>
  @ATT_STRIP@
  <div class="act">@ATT_ACTION@</div>
 </div>
 <div class="tile">
  <div class="k">温度分 · 市场什么状态</div>
  <div class="v">@TEMP_SCORE@</div>
  <div class="lbl"><span class="chip @TEMP_CLS@">@TEMP_ICON@ @TEMP_LABEL@</span></div>
  @TEMP_STRIP@
  <div class="act">@TEMP_ACTION@</div>
 </div>
</div>

<div class="oneliner">💡 @ONE_LINER@</div>

<div class="mkt">
 <div class="cell"><div class="k">@CORE_NAME@（主链）</div>
  <div class="v">@CORE_VAL@ <span style="font-size:12px">@CORE_UNIT@</span></div>
  <div class="s">日 @CORE_CHG@ · 5日 @CORE_CHG5@</div></div>
 <div class="cell"><div class="k">伦敦金</div><div class="v">@XAU_VAL@ <span style="font-size:12px">USD/oz</span></div>
  <div class="s">日 @XAU_CHG@ · 5日 @XAU_CHG5@ · USDCNY @FX@</div></div>
 <div class="cell"><div class="k">国内溢价</div><div class="v">@PREM@</div>
  <div class="s">@PREM_TAG@</div></div>
 <div class="cell"><div class="k">10Y实际利率</div><div class="v">@RR@%</div>
  <div class="s">10Y TIPS（DFII10）</div></div>
 <div class="cell"><div class="k">CFTC非商业净多</div><div class="v">@CFTC@</div>
  <div class="s">手 · 周报</div></div>
 <div class="cell"><div class="k">美元指数</div><div class="v">@USD@</div>
  <div class="s">DTWEXBGS · VIX @VIX@</div></div>
 <div class="cell"><div class="k">金油比</div><div class="v">@GO@</div>
  <div class="s">XAU÷WTI · 通胀预期 @BE@%</div></div>
</div>

<div class="grid2">
 <div class="card" style="margin-top:0">
  <h3>价格 · 期初=100</h3>
  <div class="legend"><span><i style="background:var(--c1)"></i>上海金</span>
   <span><i style="background:var(--c2)"></i>伦敦金</span>
   <span><i style="background:var(--c3)"></i>黄金ETF</span>
   <span><i style="background:var(--muted)"></i>250日均线</span></div>
  <div id="price" class="chart tall"></div>
 </div>
 <div class="card" style="margin-top:0">
  <h3>分数历史</h3>
  <div class="legend"><span><i style="background:var(--c1)"></i>关注分</span>
   <span><i style="background:var(--c2)"></i>温度分</span></div>
  <div id="scores" class="chart tall"></div>@SCORES_PLACEHOLDER@
 </div>
 <div class="card">
  <h3>关注分因子（异动贡献）</h3>
  <div id="attbars" class="chart sq"></div>
 </div>
 <div class="card">
  <h3>温度分因子（按组别着色）</h3>
  <div class="legend"><span><i style="background:var(--c1)"></i>估值</span>
   <span><i style="background:var(--c2)"></i>趋势</span>
   <span><i style="background:var(--c3)"></i>拥挤</span></div>
  <div id="tempbars" class="chart sq"></div>
 </div>
</div>

<div class="card">
 <h3>国内溢价（上海金 ÷ 伦敦金×USDCNY − 1）</h3>
 <div class="sub">正值=国内金贵（实物抢金信号）；灰色带为汇率基差噪声区（±@NOISE@%）</div>
 <div id="premium" class="chart"></div>
</div>

<div class="grid2">
 <div class="card">
  <h3>温度分构成 @TEMP_MISSING@</h3>
  <table><tr><th>组别</th><th>因子</th><th>原始值</th><th>分数</th><th>权重</th><th>方向</th><th>窗口</th></tr>
  @TEMP_ROWS@</table>
 </div>
 <div class="card">
  <h3>关注分构成</h3>
  <table><tr><th>因子</th><th>原始值</th><th>z</th><th>分数</th><th>权重</th><th>窗口</th></tr>
  @ATT_ROWS@</table>
 </div>
</div>

<div class="grid2">
 <div class="card">
  <h3>多空速览 <span class="chip @BB_CLS@">@BB_VERDICT@</span></h3>
  <div class="sub">因子高分方向解读 · 展示非预测</div>
  <table><tr><th>方向</th><th>证据（因子 原始值 (分数)）</th></tr>
   <tr><td>偏多</td><td>@BB_BULL@</td></tr>
   <tr><td>偏空/风险</td><td>@BB_BEAR@</td></tr></table>
 </div>
 <div class="card">
  <h3>黄金相关快讯（近3日）</h3>
  <div class="sub">@NEWS_TAG@ · 情绪为词典标注仅供参考</div>
  <ul class="news">@NEWS_LIS@</ul>
 </div>
</div>

<div class="card">
 <h3>数据源状态</h3>
 <table><tr><th>数据源</th><th>行数</th><th>最新</th><th>备注</th></tr>
 @SRC_ROWS@</table>
</div>

<footer>
 温度分/关注分为公开数据的自动状态描述，不构成投资建议。<br>
 方法论与口径：references/gold_framework.md · 配置与阈值：scripts/gold_config.py ·
 数据源：akshare + FRED + CFTC 官方API
</footer>
</div>

<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script>window.echarts||document.write('<script src="https://cdn.bootcdn.net/ajax/libs/echarts/5.5.0/echarts.min.js"><\\/script>')</script>
<script>window.echarts||document.write('<script src="https://unpkg.com/echarts@5/dist/echarts.min.js"><\\/script>')</script>
<script>
const DATA = @CHART_DATA@;
const C1='#2a78d6',C2='#eb6834',C3='#1baf7a',MUTED='#898781',
      GRID='#e1e0d9',BASE='#c3c2b7',INK='#0b0b0b',INK2='#52514e';
const FONT={fontFamily:'system-ui,-apple-system,"Segoe UI","PingFang SC",sans-serif'};
const TIP={trigger:'axis',axisPointer:{type:'cross',crossStyle:{color:BASE}},
   backgroundColor:'#fcfcfb',borderColor:'rgba(11,11,11,.1)',
   textStyle:{color:INK,fontSize:12}};
const AXS={axisLine:{lineStyle:{color:BASE}},axisTick:{show:false},
   axisLabel:{color:MUTED,fontSize:11},splitLine:{lineStyle:{color:GRID}}};

function mk(id,opt){const el=document.getElementById(id);
 if(!el||!window.echarts){if(el)el.innerHTML='<div class="ph">图表库加载失败(离线?) — 表格视图见下方</div>';return;}
 const c=echarts.init(el,null,{renderer:'canvas'});opt.textStyle=FONT;c.setOption(opt);
 window.addEventListener('resize',()=>c.resize());}

/* 分档标尺已内嵌到分数卡(纯CSS, 见 .strip), 无需图表库 */

/* 价格: 三序列 rebase=100 + MA250(虚线), 直接端标签 */
function line(name,data,color,dash){return {name,type:'line',data:data,showSymbol:false,
 lineStyle:{width:2,color:color,type:dash?'dashed':'solid'},itemStyle:{color:color},
 emphasis:{focus:'series'},endLabel:{show:true,formatter:name,fontSize:11,color:INK2}};}
if(DATA.dates.length){
 mk('price',{tooltip:TIP,legend:{show:false},grid:{left:44,right:70,top:12,bottom:30},
  xAxis:{type:'category',data:DATA.dates,...AXS,axisLabel:{color:MUTED,fontSize:10}},
  yAxis:{type:'value',scale:true,...AXS},
  series:[line('上海金',DATA.sge100,C1),line('伦敦金',DATA.xau100,C2),
          line('黄金ETF',DATA.etf100,C3),line('250日均线',DATA.ma250,MUTED,true)]});}

/* 分数历史 */
if(DATA.scores.length>=5){
 const sd=DATA.scores.map(r=>r[0]);
 mk('scores',{tooltip:TIP,legend:{show:false},grid:{left:34,right:16,top:12,bottom:30},
  xAxis:{type:'category',data:sd,...AXS,axisLabel:{color:MUTED,fontSize:10}},
  yAxis:{type:'value',min:0,max:100,...AXS},
  series:[{name:'关注分',type:'line',data:DATA.scores.map(r=>r[1]),showSymbol:false,
    lineStyle:{width:2,color:C1},itemStyle:{color:C1},endLabel:{show:true,formatter:'关注分',fontSize:11,color:INK2},
    markLine:{silent:true,symbol:'none',lineStyle:{type:'dashed',color:BASE},
     label:{color:MUTED,fontSize:10},data:[{yAxis:70},{yAxis:40}]}},
   {name:'温度分',type:'line',data:DATA.scores.map(r=>r[2]),showSymbol:false,
    lineStyle:{width:2,color:C2},itemStyle:{color:C2},endLabel:{show:true,formatter:'温度分',fontSize:11,color:INK2}}]});}
else{var sc=document.getElementById('scores');if(sc)sc.style.display='none';}

/* 关注分因子条形(单色) */
const AF=@ATT_DATA@;
if(AF.length){
 mk('attbars',{tooltip:{...TIP,trigger:'item'},grid:{left:110,right:44,top:8,bottom:8},
  xAxis:{type:'value',min:0,max:100,...AXS,splitLine:{show:false}},
  yAxis:{type:'category',inverse:true,data:AF.map(d=>d[0]),
   axisLine:{lineStyle:{color:BASE}},axisTick:{show:false},axisLabel:{color:INK2,fontSize:11}},
  series:[{type:'bar',data:AF.map(d=>d[1]),barWidth:12,itemStyle:{color:C1,
   borderRadius:[0,4,4,0]},label:{show:true,position:'right',fontSize:11,color:INK2,
   formatter:p=>p.value==null?'缺':p.value.toFixed(0)}}]});}

/* 温度分因子条形(按组别着色: 估值蓝/趋势橙/拥挤青/宏观灰) */
const TF=@TEMP_DATA@,GC={[100]:C1,[200]:C2,[300]:C3,[400]:MUTED};
if(TF.length){
 mk('tempbars',{tooltip:{...TIP,trigger:'item',
   formatter:p=>{const d=TF[p.dataIndex];return d[0]+'：'+(d[1]==null?'缺失':d[1].toFixed(1)+'（'+d[3]+'）');}},
  grid:{left:110,right:44,top:8,bottom:8},
  xAxis:{type:'value',min:0,max:100,...AXS,splitLine:{show:false}},
  yAxis:{type:'category',inverse:true,data:TF.map(d=>d[0]),
   axisLine:{lineStyle:{color:BASE}},axisTick:{show:false},axisLabel:{color:INK2,fontSize:11}},
  series:[{type:'bar',barWidth:12,data:TF.map(d=>({value:d[1],
    itemStyle:{color:GC[d[2]]||MUTED,borderRadius:[0,4,4,0]}})),
   label:{show:true,position:'right',fontSize:11,color:INK2,
    formatter:p=>p.value==null?'缺':p.value.toFixed(0)}}]});}

/* 国内溢价: 零轴基线 + 噪声带 */
if(DATA.premium.length){
 mk('premium',{tooltip:TIP,grid:{left:52,right:20,top:14,bottom:30},
  xAxis:{type:'category',data:DATA.dates,...AXS,axisLabel:{color:MUTED,fontSize:10}},
  yAxis:{type:'value',...AXS,axisLabel:{formatter:'{value}%'}},
  series:[{name:'国内溢价',type:'line',data:DATA.premium,showSymbol:false,
   lineStyle:{width:2,color:C1},itemStyle:{color:C1},areaStyle:{opacity:.06,color:C1},
   markLine:{silent:true,symbol:'none',lineStyle:{type:'dashed',color:BASE},
    data:[{yAxis:0}],label:{show:false}},
   markArea:{silent:true,itemStyle:{color:'rgba(137,135,129,.10)'},
    data:[[{yAxis:-DATA.premium_noise},{yAxis:DATA.premium_noise}]]}}]});}
</script></body></html>
"""
