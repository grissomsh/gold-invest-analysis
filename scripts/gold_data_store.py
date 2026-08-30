# -*- coding: utf-8 -*-
"""
黄金市场雷达 — 本地 SQLite 数据存档

两个来源的数据必须靠本地逐日积累才能得到时序:
  1. ETF份额  — 上交所接口只回吐指定单日的全市场快照, 深市为区间但需区间参数
  2. 分数自身 — score_history 让"关注分/温度分"也能画历史曲线, 并为后续
     权重校准积累样本

价格快照(price_snapshot)是廉价保险: 若日后接口收缩历史长度, 本地仍留有副本。

可独立运行打印 DB 状态:
    python3 gold_data_store.py [--stats]
"""

import os
import sqlite3
from datetime import datetime

import gold_config as C


def _connect():
    os.makedirs(C.WORKSPACE, exist_ok=True)
    conn = sqlite3.connect(C.DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS etf_shares (
        date TEXT, code TEXT, shares REAL,
        PRIMARY KEY (date, code))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS price_snapshot (
        date TEXT, series TEXT, value REAL,
        PRIMARY KEY (date, series))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS score_history (
        date TEXT PRIMARY KEY,
        attention REAL, temperature REAL, coverage REAL,
        detail TEXT, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS source_log (
        run_ts TEXT, source TEXT, ok INTEGER, rows INTEGER,
        latest TEXT, note TEXT)""")
    # v2: 快讯热度因子需日条数时序(各源只回最新若干条, 无法回补, 只能逐日积累)
    conn.execute("""CREATE TABLE IF NOT EXISTS news_log (
        date TEXT PRIMARY KEY, count INTEGER, detail TEXT, created_at TEXT)""")
    # v2: 推送去重 — 同一规则同一自然日只推一次
    conn.execute("""CREATE TABLE IF NOT EXISTS alert_log (
        rule_key TEXT PRIMARY KEY, last_date TEXT, last_sent_at TEXT)""")
    return conn


# ------------------------------------------------------------
# ETF份额
# ------------------------------------------------------------

def upsert_etf_shares(rows):
    """rows: [(date 'YYYY-MM-DD', code, shares), ...] — 幂等写入"""
    if not rows:
        return
    conn = _connect()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO etf_shares VALUES (?,?,?)", rows)
    conn.close()


def load_etf_shares(codes=None):
    """按日期升序返回 [(date, code, shares)]; codes 为空取全部"""
    conn = _connect()
    if codes:
        q = ("SELECT date, code, shares FROM etf_shares WHERE code IN (%s) "
             "ORDER BY date" % ",".join("?" * len(codes)))
        rows = conn.execute(q, codes).fetchall()
    else:
        rows = conn.execute(
            "SELECT date, code, shares FROM etf_shares ORDER BY date").fetchall()
    conn.close()
    return rows


def etf_share_dates():
    conn = _connect()
    rows = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM etf_shares").fetchall()]
    conn.close()
    return set(rows)


def total_shares_series(codes=None):
    """份额时序(升序)。只用主ETF(518880, 规模最大)自身的份额序列。
    刻意不做多ETF合计 — 各代码起始日期不同, 合计在"部分代码缺失的日期"
    会产生假跳变, 污染20日增速因子。历史用 --backfill-shares 回补。"""
    rows = load_etf_shares([C.ETF_PRIMARY])
    by_date = {}
    for d, _c, s in rows:
        by_date[d] = s or 0.0
    dates = sorted(by_date)
    return dates, [by_date[d] for d in dates]


# ------------------------------------------------------------
# 价格快照(廉价副本) 与 分数历史
# ------------------------------------------------------------

def upsert_price_snapshot(rows):
    """rows: [(date, series, value), ...] — 幂等写入"""
    if not rows:
        return
    conn = _connect()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO price_snapshot VALUES (?,?,?)", rows)
    conn.close()


def upsert_score(date, attention, temperature, coverage, detail_json):
    """同日重复运行覆盖(幂等)。detail 为完整报告 JSON 文本。"""
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO score_history VALUES (?,?,?,?,?,?)",
            (date, attention, temperature, coverage, detail_json,
             datetime.now().isoformat(timespec="seconds")))
    conn.close()


def load_scores(min_rows=1):
    """返回 [(date, attention, temperature, coverage)]; 不足 min_rows 条则返回空
    (HTML 的分数时序图需要最少样本)。"""
    conn = _connect()
    rows = conn.execute(
        "SELECT date, attention, temperature, coverage FROM score_history "
        "WHERE attention IS NOT NULL ORDER BY date").fetchall()
    conn.close()
    return rows if len(rows) >= min_rows else []


def log_sources(entries):
    """entries: [(source, ok, rows, latest, note)] — 每次运行追加一条诊断日志"""
    if not entries:
        return
    ts = datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    with conn:
        conn.executemany(
            "INSERT INTO source_log VALUES (?,?,?,?,?,?)",
            [(ts, s, int(bool(ok)), rows, latest, note)
             for s, ok, rows, latest, note in entries])
    conn.close()


# ------------------------------------------------------------
# 快讯日条数(冷启动型: 只能逐日积累, 历史不可回补)
# ------------------------------------------------------------

def upsert_news_count(date, count, detail_json=""):
    """同日重复运行覆盖(幂等)。detail 为当日命中标题的 JSON 文本。"""
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO news_log VALUES (?,?,?,?)",
            (date, int(count), detail_json,
             datetime.now().isoformat(timespec="seconds")))
    conn.close()


def news_count_series():
    """日条数时序(升序)。未积累的日期不在表中 — 对齐层视为 None(缺失≠0)。"""
    conn = _connect()
    rows = conn.execute(
        "SELECT date, count FROM news_log WHERE count IS NOT NULL ORDER BY date"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows], [float(r[1]) for r in rows]


# ------------------------------------------------------------
# 推送去重
# ------------------------------------------------------------

def alert_sent_today(rule_key, today):
    """该规则今日是否已推送过"""
    conn = _connect()
    row = conn.execute(
        "SELECT last_date FROM alert_log WHERE rule_key=?", (rule_key,)).fetchone()
    conn.close()
    return bool(row and row[0] == today)


def mark_alert_sent(rule_key, today):
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO alert_log VALUES (?,?,?)",
            (rule_key, today, datetime.now().isoformat(timespec="seconds")))
    conn.close()


# ------------------------------------------------------------
# 诊断
# ------------------------------------------------------------

def stats():
    conn = _connect()
    q = lambda sql: conn.execute(sql).fetchone()   # noqa: E731
    sh = q("SELECT COUNT(*), MIN(date), MAX(date), COUNT(DISTINCT code) FROM etf_shares")
    sc = q("SELECT COUNT(*), MIN(date), MAX(date) FROM score_history")
    px = q("SELECT COUNT(*), MIN(date), MAX(date) FROM price_snapshot")
    nw = q("SELECT COUNT(*), MIN(date), MAX(date) FROM news_log")
    al = q("SELECT COUNT(*), MAX(last_date) FROM alert_log")
    conn.close()
    return {"db_path": C.DB_PATH,
            "shares_rows": sh[0], "shares_first": sh[1], "shares_last": sh[2],
            "shares_codes": sh[3],
            "score_rows": sc[0], "score_first": sc[1], "score_last": sc[2],
            "price_rows": px[0], "price_first": px[1], "price_last": px[2],
            "news_rows": nw[0], "news_first": nw[1], "news_last": nw[2],
            "alert_rows": al[0], "alert_last": al[1]}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="黄金市场雷达数据存档状态")
    ap.parse_args()
    s = stats()
    print(f"DB: {s['db_path']}")
    print(f"etf_shares:     {s['shares_rows']} 行 ({s['shares_first']} ~ "
          f"{s['shares_last']}), {s['shares_codes']} 只")
    print(f"score_history:  {s['score_rows']} 行 ({s['score_first']} ~ {s['score_last']})")
    print(f"price_snapshot: {s['price_rows']} 行 ({s['price_first']} ~ {s['price_last']})")
    print(f"news_log:       {s['news_rows']} 行 ({s['news_first']} ~ {s['news_last']})")
    print(f"alert_log:      {s['alert_rows']} 规则 (最近推送 {s['alert_last']})")
