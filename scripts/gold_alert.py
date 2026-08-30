# -*- coding: utf-8 -*-
"""
黄金市场雷达 — 阈值推送提醒

借鉴: GoldPriceMonitor 的"阈值→提醒"概念; 通道参考 Vue 监控系统的 PushPlus。
规则(任一命中即推, 同一规则同一自然日只推一次 — SQLite alert_log 去重):
  1. att_level_up  关注分档位升级(向更严重方向跳档)
  2. temp_level    温度分档位变化(升/降档都推 — 过冷也值得知道)
  3. big_move      主链或伦敦金 |日涨跌| ≥ ALERT_MOVE_PCT 且关注分 ≥ ALERT_ATT_MIN

通道(env 配置, 可同时配多个, 未配置时静默跳过):
  GOLD_SC_SENDKEY       Server酱  → POST sctapi.ftqq.com/{key}.send
  GOLD_PUSHPLUS_TOKEN   PushPlus  → POST pushplus.plus/send (channel=wechat)
  GOLD_BARK_URL         Bark      → POST {url} (如 https://api.day.app/{key})

对比基准: 关注/温度档位与 score_history 中"今日之前的最近一条"比较,
因此首次运行(无历史)不会触发档位规则 — 先积累几天基线。
"""

import os
from datetime import datetime

import gold_config as C
import gold_data_store as store
import gold_report as R
import gold_scoring as G

try:
    import requests
except Exception:                                          # pragma: no cover
    requests = None

RULE_ATT_UP = "att_level_up"
RULE_TEMP = "temp_level"
RULE_BIG_MOVE = "big_move"


def _level_idx(label, levels):
    """标签 → 档位序号(0=最高档); 未知标签 → None"""
    for k, lv in enumerate(levels):
        if lv[1] == label:
            return k
    return None


def _prev_scores(today):
    """score_history 中今日之前的最近一条 → (att, temp) 分数"""
    rows = store.load_scores(min_rows=1)
    prev = [r for r in rows if r[0] < today]
    return (prev[-1][1], prev[-1][2]) if prev else (None, None)


def evaluate(rep, today):
    """规则评估 → [(rule_key, 标题)]。分数为 None 时对应规则一律不触发。"""
    att, temp = rep["attention"], rep["temperature"]
    fires = []
    p_att, p_temp = _prev_scores(today)

    if att["score"] is not None and p_att is not None:
        cur = _level_idx(att["label"], C.ATTENTION_LEVELS)
        prv = _level_idx(G.level_of(p_att, C.ATTENTION_LEVELS)[0],
                         C.ATTENTION_LEVELS)
        if cur is not None and prv is not None and cur < prv:
            fires.append((RULE_ATT_UP,
                          f"关注分升档 {att['label']}{att['icon']} {att['score']}"))
    if temp["score"] is not None and p_temp is not None:
        cur = _level_idx(temp["label"], C.TEMPERATURE_LEVELS)
        prv = _level_idx(G.level_of(p_temp, C.TEMPERATURE_LEVELS)[0],
                         C.TEMPERATURE_LEVELS)
        if cur is not None and prv is not None and cur != prv:
            fires.append((RULE_TEMP,
                          f"温度分变档 {temp['label']}{temp['icon']} {temp['score']}"))

    chgs = [v for v in (rep["market"].get("core_chg1d"),
                        rep["market"].get("xau_chg1d")) if v is not None]
    if chgs and att["score"] is not None and att["score"] >= C.ALERT_ATT_MIN:
        big = max(chgs, key=abs)
        if abs(big) >= C.ALERT_MOVE_PCT:
            fires.append((RULE_BIG_MOVE,
                          f"金价单日{big:+.1f}% 关注{att['score']} {att['label']}{att['icon']}"))
    return fires


def build_desp(rep):
    """推送正文: 一句话结论 + 双分数 + 档位动作"""
    att, temp = rep["attention"], rep["temperature"]
    return (f"{R.one_liner(rep)}\n\n"
            f"关注分 {att['score']} {att['label']}{att['icon']} — {att['action']}\n"
            f"温度分 {temp['score']} {temp['label']}{temp['icon']} — {temp['action']}\n\n"
            f"数据截至 {rep['report_date']} · "
            f"{datetime.now().strftime('%H:%M')} 生成\n"
            f"状态描述, 不构成投资建议")


def _post(url, **kw):
    r = requests.post(url, timeout=10, **kw)
    r.raise_for_status()
    return True, r.text[:60].replace("\n", " ")


def _channels():
    """env 已配置的通道 [(名称, fn(title, desp) -> (ok, msg))]"""
    ch = []
    key = os.environ.get(C.ALERT_SC_SENDKEY_ENV, "").strip()
    if key:
        ch.append(("Server酱", lambda t, d: _post(
            f"https://sctapi.ftqq.com/{key}.send", data={"title": t, "desp": d})))
    token = os.environ.get(C.ALERT_PUSHPLUS_ENV, "").strip()
    if token:
        ch.append(("PushPlus", lambda t, d: _post(
            "https://www.pushplus.plus/send",
            json={"token": token, "title": t, "content": d, "channel": "wechat"})))
    bark = os.environ.get(C.ALERT_BARK_ENV, "").strip()
    if bark:
        ch.append(("Bark", lambda t, d: _post(
            bark.rstrip("/"), json={"title": t, "body": d})))
    return ch


def dispatch(rep, today):
    """评估规则→推送→按日去重落库。未配置通道/未命中规则时打印说明。"""
    if requests is None:
        print("⚠️ requests 不可用, 无法推送")
        return
    chs = _channels()
    if not chs:
        print(f"ℹ️ 未配置推送通道(设 {C.ALERT_SC_SENDKEY_ENV} / "
              f"{C.ALERT_PUSHPLUS_ENV} / {C.ALERT_BARK_ENV} 任一), 跳过")
        return
    fires = evaluate(rep, today)
    if not fires:
        print("✅ 推送: 未命中提醒规则(档位未变化且无大异动)")
        return
    desp = build_desp(rep)
    for rule_key, title in fires:
        if store.alert_sent_today(rule_key, today):
            print(f"⏭️ {rule_key} 今日已推送过, 跳过")
            continue
        any_ok = False
        for name, fn in chs:
            try:
                ok, msg = fn(title, desp)
                any_ok = any_ok or ok
                print(f"{'✅' if ok else '⚠️ '} 推送[{name}] {title} → {msg}")
            except Exception as e:                        # noqa: BLE001
                print(f"⚠️ 推送[{name}] 失败: {type(e).__name__} {str(e)[:80]}")
        if any_ok:                    # 全部通道失败时不记已推送, 明日/明次可重试
            store.mark_alert_sent(rule_key, today)
