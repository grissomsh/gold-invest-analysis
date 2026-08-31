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
            "ma250_dev", "mom20", "mom60", "mom120",
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
    "vix": 1, "breakeven": 1, "cftc_net": 1,
    # (避险/抗通胀/申购/投机多头需求; CFTC 拥挤的双面性在框架文档注明)
    "news_heat": 0, "ret1d_z": 0, "ret5d_z": 0, "vol_spike": 0,
    "rv20_pct": -1,                                          # 高波动=追加风险高
    "gold_m2": -1,                                           # 估值贵=风险证据
}


# 因子悬停/速查说明(HTML 报告): 口径 + 高分解读。冷方向因子"分数高"=原始值
# 处于低位(反向分位), 解读按"分数高代表什么"来写。
FACTOR_TIPS = {
    # 温度-估值
    "gold_m2":    "口径: 伦敦金÷美国M2, 10年滚动分位。高分=相对货币供应很贵——温度分最核心的估值锚, 长期看均值回归。",
    "gold_silver": "口径: 金银比(XAU÷XAG), 3年滚动分位, 反向。高分=金银比处于低位=白银端更亢奋, 贵金属情绪整体偏热。",
    "gold_oil":   "口径: 金油比(XAU÷WTI), 3年滚动分位, 反向。高分=金相对原油便宜——实体需求端不弱, 黄金估值不算贵。",
    "real_rate":  "口径: 10Y TIPS 实际利率(DFII10), 10年分位, 反向。高分=实际利率处于低位=持有黄金的机会成本低(利多环境)。2022后央行购金使该锚弱化, 权重刻意压低。",
    # 温度-趋势
    "ma250_dev":  "口径: 现价相对250日均线的偏离, 分段映射。高分=价格深入年线上方, 中期趋势强; 低于40分=价格在年线下方。",
    "mom20":      "口径: 20日收益的3年滚动分位。高分=近一个月涨幅在自身历史上罕见——最敏感的短视角动能。",
    "mom60":      "口径: 60日收益的3年滚动分位。季度级动能。",
    "mom120":     "口径: 120日收益的3年滚动分位。半年级动能, 最钝但最能代表趋势延续。",
    # 温度-拥挤
    "cftc_net":   "口径: CFTC非商业净多头(投机资金)全历史分位, 周频。高分=投机多头拥挤——上涨动能强但踩踏风险同步积聚(拥挤不等于要跌, 是双向信息)。",
    "rv20_pct":   "口径: 20日已实现波动率的3年分位。高分=波动罕见地大——无论单边急涨还是恐慌急跌, 此时追加仓位的风险都高。",
    # 温度-宏观
    "usd_idx":    "口径: 美元广义贸易加权指数(DTWEXBGS), 3年分位, 反向。高分=美元相对自身历史弱——美元弱则美元计价的金受益。注意: 分数高≠美元点位高。",
    "vix":        "口径: VIX恐慌指数收盘, 3年分位。高分=市场恐慌情绪罕见地高, 避险资金流入支撑金价。",
    "breakeven":  "口径: 通胀预期=美债名义10Y(DGS10)−实际10Y(DFII10), 3年分位。高分=市场定价的长期通胀补偿高, 抗通胀配置需求大。",
    # 关注分
    "ret1d_z":    "口径: 单日收益相对近250个交易日自身分布的z分数(自排除窗口), tanh压缩到0-100。高分=今天的单日波动相对自身历史罕见——'今天有事发生'的最直接信号。",
    "ret5d_z":    "口径: 5日收益的z分数(250 obs)。捕捉一周级别的趋势突变, 比单日更稳。",
    "vol_spike":  "口径: 0.6×RV20水平分位 + 0.4×分位20日抬升幅度。高分=波动不仅罕见地高, 还在快速抬升——异动常先于价格体现。",
    "news_heat":  "口径: 当日黄金相关快讯条数对本地90日积累的分位(新浪/财联社/金属网三源)。高分=今日新闻面显著热闹——事件驱动的'该看'信号。积累<20日时缺失。",
    "ath_prox":   "口径: 现价距历史新高的距离, 分段映射。高分=逼近或创新高——强势本身是事件; 权重刻意压低, 因长牛中该值长期高位会抬高关注分底线。",
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


# WGC/IMF 国名 → 中文速记(未收录时用逗号前段)
NAME_CN = {
    "Poland, Republic of": "波兰", "Uzbekistan, Republic of": "乌兹别克",
    "Kazakhstan, Republic of": "哈萨克", "China, People's Republic of": "中国",
    "Euro Area (EA)": "欧元区", "Turkey": "土耳其", "Russian Federation": "俄罗斯",
    "India": "印度", "United States": "美国", "Germany": "德国", "Italy": "意大利",
    "France": "法国", "Japan": "日本", "Switzerland": "瑞士",
    "Netherlands, The": "荷兰", "Ghana": "加纳", "IMF": "IMF",
    "United Kingdom": "英国", "Spain": "西班牙", "Austria": "奥地利",
    "Belgium": "比利时", "Saudi Arabia, Kingdom of": "沙特",
    "Czech Republic": "捷克", "Thailand": "泰国", "Singapore": "新加坡",
    "Qatar": "卡塔尔", "Iraq": "伊拉克", "Poland": "波兰",
    "Hungary": "匈牙利", "Egypt, Arab Rep. of": "埃及", "Philippines": "菲律宾",
    "Indonesia": "印尼", "Malaysia": "马来西亚", "Vietnam": "越南",
    "Brazil": "巴西", "Mexico": "墨西哥", "Canada": "加拿大",
    "Australia": "澳大利亚", "Korea, Republic of": "韩国",
    "China, P.R.: Mainland": "中国", "China, P.R.: Hong Kong": "中国香港",
    "Bulgaria": "保加利亚", "Romania": "罗马尼亚", "Jordan": "约旦",
    "Kyrgyz Republic": "吉尔吉斯", "Tajikistan, Rep. of": "塔吉克",
    "Azerbaijan, Rep. of": "阿塞拜疆", "Belarus, Rep. of": "白俄罗斯",
    "Serbia, Rep. of": "塞尔维亚", "Colombia": "哥伦比亚", "Morocco": "摩洛哥",
    "State Oil Fund of the Republic of Azerbaijan (SOFAZ)": "阿塞拜疆SOFAZ",
}


def _cn_name(name):
    return NAME_CN.get(name, str(name).split(",")[0])


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
    print(f"{_pad('国内申购热度', 10)} 20日份额增速 {_fmt_pct(m.get('share_20d'), 1)}"
          f"（仅展示, 不进分数）")
    cbg = rep.get("cb_gold", {})
    cb = cbg.get("cn")
    if cb:
        d12 = (f"   近12月{cb['d12_tonne']:+.1f}吨" if cb.get("d12_tonne") is not None else "")
        share = (f"   占外储~{_fmt(cb['share_pct'], 1)}%(按最新金价估算)"
                 if cb.get("share_pct") is not None else "")
        print(f"{_pad('央行购金', 10)} 中国 {_fmt(cb['tonnes'], 0)}吨({cb['latest_date']})"
              f"{d12}   连增{cb['streak']}月{share}")
    else:
        print(f"{_pad('央行购金', 10)} SAFE 数据不可得")
    gl = cbg.get("global")
    if gl:
        fmt5 = lambda xs: " ".join(f"{_cn_name(n)}{v:+.0f}" for n, v in xs[:4])
        print(f"{_pad('各国央行12M', 10)} 全球{gl['world_12m']:+.0f}吨"
              f"({gl['window'][0]}~{gl['window'][1]})   "
              f"增:{fmt5(gl['top_buy'])}   减:{fmt5(gl['top_sell'])}")
        if gl.get("holders"):
            h5 = gl["holders"][:5]
            print(f"{_pad('持仓TOP5', 10)} "
                  + " ".join(f"{_cn_name(n)}{t:.0f}" for _r, n, t, _p in h5)
                  + f"   (WGC文件, 至 {gl.get('holders_asof') or '—'})")

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
    """因子表 HTML 行(末列=口径/解读, 与悬停 tooltip 同源 FACTOR_TIPS)"""
    out = []
    for r in rows:
        tip = _esc(FACTOR_TIPS.get(r["key"], ""))
        if kind == "temp":
            cells = [r["组"] or "—", _esc(r["名"]), _raw(r["key"], r["raw"]),
                     _fmt(r["score"], 1), f"{r['weight']}%", r["方向"], r["窗"]]
        else:
            z = f"{r['z']:+.2f}" if r.get("z") is not None else "—"
            cells = [_esc(r["名"]), _raw(r["key"], r["raw"]), z,
                     _fmt(r["score"], 1), f"{r['weight']}%", r["窗"]]
        cls = ' class="miss"' if r["score"] is None else ""
        tds = "".join(f"<td>{c}</td>" for c in cells) + f'<td class="tip">{tip}</td>'
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
    cbg = rep.get("cb_gold", {})
    cb = cbg.get("cn")
    gl = cbg.get("global")
    if cb:
        cb_rows = (f"<tr><td>最新储备</td><td><b>{_fmt(cb['tonnes'], 0)} 吨</b>"
                   f"（{cb['latest_date']}）</td></tr>"
                   + (f"<tr><td>近12月净增</td><td>{cb['d12_tonne']:+.1f} 吨</td></tr>"
                      if cb.get("d12_tonne") is not None else "")
                   + f"<tr><td>连续增持</td><td>{cb['streak']} 个月</td></tr>"
                   + (f"<tr><td>占外储比重</td><td>≈{_fmt(cb['share_pct'], 1)}%"
                      "（按最新伦敦金估算）</td></tr>"
                      if cb.get("share_pct") is not None else ""))
    else:
        cb_rows = '<tr><td colspan="2" style="color:var(--muted)">SAFE 数据不可得</td></tr>'
    if gl:
        cb_glob = (f"全球央行近12月净购金 <b>{gl['world_12m']:+.0f} 吨</b>"
                   f"（{gl['window'][0]} ~ {gl['window'][1]}，IMF/WGC 口径）"
                   f'<span style="color:var(--muted)"> · 源: {gl["changes_file"]}</span>')
        wb = sorted(gl["top_buy"] + gl["top_sell"], key=lambda x: -abs(x[1]))[:10]
        world_rows = "".join(
            f"<tr><td>{_esc(_cn_name(n))}</td>"
            f'<td style="text-align:right;color:{"var(--c1)" if v >= 0 else "var(--c2)"}">'
            f"{v:+.1f}</td></tr>" for n, v in wb)
    else:
        cb_glob = ('<span style="color:var(--muted)">WGC 文件未导入 — 将 Goldhub 下载的'
                   ' Changes_latest_*.xlsx / World_official_gold_holdings_*.xlsx 放入 '
                   'workspace/data/ 即显示各国榜</span>')
        world_rows = ""
    holder_rows = "".join(
        f"<tr><td>{r}</td><td>{_esc(_cn_name(n))}</td>"
        f'<td style="text-align:right">{t:,.0f}</td>'
        f'<td style="text-align:right">{_fmt(p, 1) if p is not None else "—"}</td></tr>'
        for r, n, t, p in (gl.get("holders") or [])[:10]) if gl else ""

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
        "@CB_ROWS@": cb_rows,
        "@CB_GLOB@": cb_glob,
        "@CB_WORLD_ROWS@": world_rows,
        "@CB_HOLDER_ROWS@": holder_rows,
        "@NOISE@": _fmt(C.PREMIUM_NOISE, 1),
        "@SCORES_PLACEHOLDER@": "" if len(scores) >= 5 else
        '<div class="ph">分数历史需本地逐日积累（≥5个运行日后可画曲线）</div>',
        "@ATT_DATA@": json.dumps(
            [[f["名"], f["score"], FACTOR_TIPS.get(f["key"], ""),
              _raw(f["key"], f["raw"]), f"{f['weight']}%"] for f in att["factors"]],
            ensure_ascii=False).replace("</", "<\\/"),
        "@TEMP_DATA@": json.dumps(
            [[f["名"], f["score"], grp_code.get(f["组"], 0), f["方向"],
              FACTOR_TIPS.get(f["key"], ""), _raw(f["key"], f["raw"]),
              f"{f['weight']}%"] for f in temp["factors"]],
            ensure_ascii=False).replace("</", "<\\/"),
    }

    html = _HTML_TEMPLATE
    for k, v in tokens.items():
        html = html.replace(k, v)
    if gl:
        wb10 = sorted(gl["top_buy"] + gl["top_sell"], key=lambda x: -abs(x[1]))[:10]
        cbworld = {"names": [_cn_name(n) for n, _v in wb10],
                   "vals": [v for _n, v in wb10]}
    else:
        cbworld = {"names": [], "vals": []}
    payload = json.dumps({
        "dates": chart.get("dates") or [],
        "sge100": chart.get("sge100") or [],
        "etf100": chart.get("etf100") or [],
        "xau100": chart.get("xau100") or [],
        "ma250": chart.get("ma250") or [],
        "premium": chart.get("premium") or [],
        "premium_noise": C.PREMIUM_NOISE,
        "cb": chart.get("cb") or {},
        "cbworld": cbworld,
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
.wrap{max-width:1280px;margin:0 auto;padding:24px 20px 48px}
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
.chart.wide{height:420px}.chart.tall{height:340px}.chart.sq{height:260px}
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
td.tip{font-size:11.5px;color:var(--ink2);line-height:1.6;min-width:200px}
.howto{font-size:12px;color:var(--ink2);margin:2px 0 6px;line-height:1.6}
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
 <div class="card" style="grid-column:1/-1">
  <h3>价格 · 期初=100</h3>
  <div class="legend"><span><i style="background:var(--c1)"></i>上海金</span>
   <span><i style="background:var(--c2)"></i>伦敦金</span>
   <span><i style="background:var(--c3)"></i>黄金ETF</span>
   <span><i style="background:var(--muted)"></i>250日均线</span></div>
  <div id="price" class="chart wide"></div>
 </div>
 <div class="card" style="grid-column:1/-1">
  <h3>分数历史</h3>
  <div class="legend"><span><i style="background:var(--c1)"></i>关注分</span>
   <span><i style="background:var(--c2)"></i>温度分</span></div>
  <div id="scores" class="chart wide"></div>@SCORES_PLACEHOLDER@
 </div>
 <div class="card">
  <h3>关注分因子（异动贡献）</h3>
  <div class="howto">悬停因子条（或见下方构成表"口径/解读"列）查看口径。分数=该项异动的罕见度，越高说明"今天越有事"；缺失表示数据未积累或源不可得（权重自动摊给其余因子）。</div>
  <div id="attbars" class="chart sq"></div>
 </div>
 <div class="card">
  <h3>温度分因子（按组别着色）</h3>
  <div class="howto">悬停因子条（或见下方构成表"口径/解读"列）查看口径。条长=该维度在自身历史中的"热"位置；冷方向因子（金银比/金油比/实际利率/美元）分数高 = 压制项处于低位，同样是"热"的证据。组别：<span style="color:var(--c1)">估值蓝</span> / <span style="color:var(--c2)">趋势橙</span> / <span style="color:var(--c3)">拥挤青</span> / <span style="color:var(--muted)">宏观灰</span>。</div>
  <div id="tempbars" class="chart sq"></div>
 </div>
</div>

<div class="grid2">
 <div class="card">
  <h3>关注分构成</h3>
  <table><tr><th>因子</th><th>原始值</th><th>z</th><th>分数</th><th>权重</th><th>窗口</th><th>口径/解读</th></tr>
  @ATT_ROWS@</table>
 </div>
 <div class="card">
  <h3>温度分构成 @TEMP_MISSING@</h3>
  <table><tr><th>组别</th><th>因子</th><th>原始值</th><th>分数</th><th>权重</th><th>方向</th><th>窗口</th><th>口径/解读</th></tr>
  @TEMP_ROWS@</table>
 </div>
</div>

<div class="card">
 <h3>国内溢价（上海金 ÷ 伦敦金×USDCNY − 1）</h3>
 <div class="sub">正值=国内金贵（实物抢金信号）；灰色带为汇率基差噪声区（±@NOISE@%）</div>
 <div id="premium" class="chart"></div>
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
 <div class="card" style="grid-column:1/-1">
  <h3>央行购金 · 中国（月度）</h3>
  <div class="sub">SAFE 官方储备 · 月频慢变量，仅展示不进分数 · 上图总储备，下图当月净购金（蓝=增持 / 橙=减持）· 近60个月</div>
  <div id="cbchart" class="chart"></div>
  <table style="margin-top:10px;max-width:480px"><tr><th>指标</th><th>数值</th></tr>
  @CB_ROWS@</table>
 </div>
 <div class="card" style="grid-column:1/-1">
  <h3>央行购金 · 全球各国</h3>
  <div class="sub">@CB_GLOB@</div>
  <div class="grid2" style="margin-top:6px">
   <div>
    <div class="howto">近12月净购金 TOP（正=增持蓝 / 负=减持橙，吨）</div>
    <div id="cbworld" class="chart sq"></div>
   </div>
   <div>
    <div class="howto">官方黄金持仓 TOP10（吨 / 占外储比重，WGC Goldhub 文件）</div>
    <table><tr><th>#</th><th>国家</th><th style="text-align:right">持仓(吨)</th><th style="text-align:right">占外储</th></tr>
    @CB_HOLDER_ROWS@</table>
   </div>
  </div>
  <table style="margin-top:8px;max-width:520px"><tr><th>近12月变化榜</th><th style="text-align:right">净购金(吨)</th></tr>
  @CB_WORLD_ROWS@</table>
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
 mk('price',{tooltip:TIP,grid:{left:44,right:112,top:12,bottom:30},
  xAxis:{type:'category',data:DATA.dates,...AXS,axisLabel:{color:MUTED,fontSize:10}},
  yAxis:{type:'value',scale:true,...AXS},
  legend:{orient:'vertical',right:6,top:16,itemWidth:12,itemHeight:8,
   textStyle:{color:INK2,fontSize:11},data:['上海金','伦敦金','黄金ETF','250日均线']},
  series:[{name:'上海金',type:'line',data:DATA.sge100,showSymbol:false,
    lineStyle:{width:2,color:C1},itemStyle:{color:C1},emphasis:{focus:'series'}},
   {name:'伦敦金',type:'line',data:DATA.xau100,showSymbol:false,
    lineStyle:{width:2,color:C2},itemStyle:{color:C2},emphasis:{focus:'series'}},
   {name:'黄金ETF',type:'line',data:DATA.etf100,showSymbol:false,
    lineStyle:{width:2,color:C3},itemStyle:{color:C3},emphasis:{focus:'series'}},
   {name:'250日均线',type:'line',data:DATA.ma250,showSymbol:false,
    lineStyle:{width:2,color:MUTED,type:'dashed'},itemStyle:{color:MUTED},emphasis:{focus:'series'}}]});}

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

/* 因子条形悬停说明: [名,分数,...,口径tip,原始值,权重] → 富 tooltip */
const TIPX={trigger:'item',confine:true,
 backgroundColor:'#fcfcfb',borderColor:'rgba(11,11,11,.1)',
 textStyle:{color:INK,fontSize:12},
 extraCssText:'max-width:380px;white-space:normal;line-height:1.65;box-shadow:0 2px 12px rgba(11,11,11,.10)'};
function tipHTML(name,dir,d5,score,raw,wt,tip){
 const s=(score==null?'<b>缺失</b>（权重自动摊给其余因子）':'<b>'+score.toFixed(1)+'</b>');
 return '<b>'+name+'</b> <span style="color:#898781">'+dir+' · 权重 '+wt+'</span><br>'
  +'原始值 '+raw+' · 分数 '+s
  +(tip?'<br><span style="color:#52514e">'+tip+'</span>':'');}

/* 关注分因子条形(单色) */
const AF=@ATT_DATA@;
if(AF.length){
 mk('attbars',{tooltip:{...TIPX,formatter:p=>{const d=AF[p.dataIndex];
   return tipHTML(d[0],'异动',null,d[1],d[3],d[4],d[2]);}},
  grid:{left:110,right:44,top:8,bottom:8},
  xAxis:{type:'value',min:0,max:100,...AXS,splitLine:{show:false}},
  yAxis:{type:'category',inverse:true,data:AF.map(d=>d[0]),
   axisLine:{lineStyle:{color:BASE}},axisTick:{show:false},axisLabel:{color:INK2,fontSize:11}},
  series:[{type:'bar',data:AF.map(d=>d[1]),barWidth:12,itemStyle:{color:C1,
   borderRadius:[0,4,4,0]},label:{show:true,position:'right',fontSize:11,color:INK2,
   formatter:p=>p.value==null?'缺':p.value.toFixed(0)}}]});}

/* 温度分因子条形(按组别着色: 估值蓝/趋势橙/拥挤青/宏观灰) */
const TF=@TEMP_DATA@,GC={[100]:C1,[200]:C2,[300]:C3,[400]:MUTED};
if(TF.length){
 mk('tempbars',{tooltip:{...TIPX,formatter:p=>{const d=TF[p.dataIndex];
   return tipHTML(d[0],d[3],null,d[1],d[5],d[6],d[4]);}},
  grid:{left:110,right:44,top:8,bottom:8},
  xAxis:{type:'value',min:0,max:100,...AXS,splitLine:{show:false}},
  yAxis:{type:'category',inverse:true,data:TF.map(d=>d[0]),
   axisLine:{lineStyle:{color:BASE}},axisTick:{show:false},axisLabel:{color:INK2,fontSize:11}},
  series:[{type:'bar',barWidth:12,data:TF.map(d=>({value:d[1],
    itemStyle:{color:GC[d[2]]||MUTED,borderRadius:[0,4,4,0]}})),
   label:{show:true,position:'right',fontSize:11,color:INK2,
    formatter:p=>p.value==null?'缺':p.value.toFixed(0)}}]});}

/* 央行购金小倍数: 上折线=总储备(吨), 下柱=当月净购金(蓝=增持/橙=减持) */
const CB=DATA.cb||{};
if(CB.dates && CB.dates.length>=3){
 const cd=CB.dates, ct=CB.tonnes, cn=CB.net;
 mk('cbchart',{tooltip:{...TIPX,formatter:p=>{
   const i=p.dataIndex;
   return cd[i]+'<br>总储备 <b>'+ct[i]+'</b> 吨 · 当月净增 <b>'
     +(cn[i]==null?'—':(cn[i]>0?'+':'')+cn[i].toFixed(1))+'</b> 吨';}},
  grid:[{left:46,right:16,top:10,height:'50%'},{left:46,right:16,top:'66%',height:'26%'}],
  xAxis:[{type:'category',data:cd,...AXS,axisLabel:{color:MUTED,fontSize:10,interval:11}},
         {type:'category',gridIndex:1,data:cd,...AXS,axisLabel:{color:MUTED,fontSize:10,interval:11}}],
  yAxis:[{type:'value',scale:true,...AXS},
         {type:'value',gridIndex:1,...AXS}],
  series:[
   {name:'总储备',type:'line',data:ct,showSymbol:false,
    lineStyle:{width:2,color:C1},itemStyle:{color:C1},
    areaStyle:{opacity:.06,color:C1}},
   {name:'当月净购金',type:'bar',xAxisIndex:1,yAxisIndex:1,barWidth:'60%',
    data:cn.map(v=>({value:v,itemStyle:{color:v==null?MUTED:(v>=0?C1:C2),
     borderRadius:v>=0?[3,3,0,0]:[0,0,3,3]}})),
    markLine:{silent:true,symbol:'none',lineStyle:{type:'dashed',color:BASE},
     label:{show:false},data:[{yAxis:0}]}}]});
}

/* 各国近12月净购金 TOP10: 横向条形, 蓝=增持/橙=减持 */
const CW=DATA.cbworld||{names:[],vals:[]};
if(CW.names.length){
 mk('cbworld',{tooltip:{...TIPX,formatter:p=>{
   const v=CW.vals[p.dataIndex];
   return CW.names[p.dataIndex]+' 近12月净购金 <b>'+(v>0?'+':'')+v.toFixed(1)+'</b> 吨';}},
  grid:{left:76,right:44,top:8,bottom:8},
  xAxis:{type:'value',...AXS,splitLine:{show:false}},
  yAxis:{type:'category',inverse:true,data:CW.names,
   axisLine:{lineStyle:{color:BASE}},axisTick:{show:false},axisLabel:{color:INK2,fontSize:11}},
  series:[{type:'bar',barWidth:12,data:CW.vals.map(v=>({value:v,
   itemStyle:{color:v>=0?C1:C2,borderRadius:v>=0?[0,3,3,0]:[3,0,0,3]}})),
   label:{show:true,position:'right',fontSize:11,color:INK2,
    formatter:p=>(p.value>0?'+':'')+p.value.toFixed(0)}}]});}

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
