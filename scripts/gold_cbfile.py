# -*- coding: utf-8 -*-
"""
黄金市场雷达 — WGC Goldhub 手动导入数据解析(各国央行购金)

数据来源: gold.org 注册(免费)后下载, 每月更新, 放入 workspace/data/:
  Changes_latest_as_of_*.xlsx              Monthly表: 各国央行月度净购金(吨), 2002-01起
  World_official_gold_holdings_as_of_*.xlsx  PDF表: 各国最新持仓榜(吨 / 占外储比重)

口径(与 references/gold_framework.md §6.12 一致): 各国央行购金是月频慢变量,
仅作展示区块, 不进双分数。文件缺失时区块整体缺席, 不影响分数。
解析只用标准库(zipfile + ElementTree), 无 openpyxl 依赖。
"""

import glob
import os
import re
import zipfile
from xml.etree import ElementTree as ET

import gold_config as C

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_CHANGES_GLOB = "Changes_latest_*.xlsx"
_HOLDINGS_GLOB = "World_official_gold_holdings_*.xlsx"
# Monthly表的非国家行(表头说明标签)
_SKIP_LABELS = ('=>', 'Country Lookup', 'Last month', 'This month', 'month column')


def _open_sheet(path, sheet_name):
    """xlsx → (shared_strings, 目标sheet的Element)。找不到sheet → None"""
    z = zipfile.ZipFile(path)
    try:
        ss = ["".join(x.itertext())
              for x in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS)]
    except KeyError:
        ss = []
    order = re.findall(r'<sheet name="([^"]+)"[^>]*r:id="(rId\d+)"',
                       z.read("xl/workbook.xml").decode("utf-8", "ignore"))
    rels = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="worksheets/([^"]+)"',
                           z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "ignore")))
    rid = dict(order).get(sheet_name)
    if rid is None or rid not in rels:
        return None
    return ET.fromstring(z.read("xl/worksheets/" + rels[rid])), ss


def _row_cells(row, ss):
    """row元素 → {列字母: 值}(数字为float, 共享字符串已还原)"""
    out = {}
    for c in row.findall("m:c", NS):
        col = re.match(r"([A-Z]+)", c.get("r", "")).group(1)
        v = c.find("m:v", NS)
        if v is None or v.text is None:
            continue
        val = ss[int(v.text)] if c.get("t") == "s" else v.text
        if c.get("t") == "str" or re.match(r"^-?[\d.]+(?:E[-+]?\d+)?$", str(val)) is None:
            out[col] = str(val)
        else:
            out[col] = float(val)
    return out


def _serial_month(n):
    """Excel日期序列 → 'YYYY-MM'"""
    import datetime
    d = datetime.date(1899, 12, 30) + datetime.timedelta(days=int(n))
    return d.isoformat()[:7]


def find_changes_file():
    p = os.path.join(C.WORKSPACE, "data")
    hits = sorted(glob.glob(os.path.join(p, _CHANGES_GLOB)))
    return hits[-1] if hits else None


def find_holdings_file():
    p = os.path.join(C.WORKSPACE, "data")
    hits = sorted(glob.glob(os.path.join(p, _HOLDINGS_GLOB)))
    return hits[-1] if hits else None


def parse_changes(path):
    """Changes_latest Monthly表 → {"months":[...升序], "changes": {国家: {月: 净购金吨}}}
    单元格缺失 = 当月未披露(None), 与 0(披露无变化) 区分。"""
    got = _open_sheet(path, "Monthly")
    if got is None:
        return None
    root, ss = got
    rows = root.findall(".//m:row", NS)

    # 月份映射行: 含≥8个日期序列号(>30000)的行
    col_month, seen = {}, 0
    for r in rows[:10]:
        d = _row_cells(r, ss)
        serials = {k: v for k, v in d.items()
                   if isinstance(v, float) and v > 30000 and v < 60000}
        if len(serials) >= 8:
            col_month = {k: _serial_month(v) for k, v in serials.items()}
            seen = rows.index(r)
            break
    if not col_month:
        return None
    months = sorted(set(col_month.values()))

    changes = {}
    for r in rows[seen + 1:]:
        d = _row_cells(r, ss)
        name = d.get("A")
        if not isinstance(name, str) or not name.strip():
            continue
        if any(lbl in name for lbl in _SKIP_LABELS):
            continue
        series = {}
        for col, month in col_month.items():
            v = d.get(col)
            if isinstance(v, float):
                series[month] = round(v, 3)
        if series:
            changes[name.strip()] = series
    # WGC 文件把土耳其列了两行: 'Turkey'(官方榜口径, 与 holdings 表同名)和
    # 'Türkiye, Republic of'(IMF模板口径)——两套数值并存会重复计入全球合计。
    # 保留与官方榜同名的 'Turkey', 剔除模板名行(见 framework §6.12)。
    for dup in ("Türkiye, Republic of",):
        changes.pop(dup, None)
    return {"months": months, "changes": changes}


def parse_holdings(path):
    """holdings PDF表 → {"asof_max":"YYYY-MM-DD", "rows":[(排名,国家,吨,占外储|None)]}"""
    got = _open_sheet(path, "PDF")
    if got is None:
        return None
    root, ss = got
    rows = root.findall(".//m:row", NS)
    out, asof = [], None
    for r in rows:
        d = _row_cells(r, ss)
        vals = list(d.values())
        if len(vals) < 3 or not isinstance(vals[0], float):
            continue
        rank = int(vals[0])
        name, tonnes = vals[1], vals[2]
        if not isinstance(name, str) or not isinstance(tonnes, float):
            continue
        name = re.sub(r"\d+\)$", "", name).strip()   # 脚注标记: 'Turkey5)'→'Turkey'
        pct = None
        if len(vals) > 3 and isinstance(vals[3], float):
            pct = round(vals[3] * 100, 1)
        for v in vals[4:]:
            if isinstance(v, float) and 40000 < v < 60000:
                import datetime
                asof_i = (datetime.date(1899, 12, 30)
                          + datetime.timedelta(days=int(v))).isoformat()
                asof = max(asof or "", asof_i)
        out.append((rank, name.strip(), round(tonnes, 1), pct))
    return {"rows": out, "asof_max": asof}


def window_metrics(data, window=12):
    """近 window 个月的全球/分国净购金。返回 {"from","to","world","by"}"""
    months = data["months"][-window:]
    by = {}
    for name, series in data["changes"].items():
        s = sum(series.get(m) or 0.0 for m in months)
        by[name] = round(s, 1)
    world = round(sum(by.values()), 1)
    return {"from": months[0], "to": months[-1], "world": world, "by": by}
