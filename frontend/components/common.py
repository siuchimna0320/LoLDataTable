"""跨頁共用：篩選解析、英雄頭像、表格樣式、KPI 卡等。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import pandas as pd
from dash import html

from backend import config, data_access
from frontend import theme

POSITION_ZH = {"top": "上路", "jng": "打野", "mid": "中路",
               "bot": "下路", "sup": "輔助"}


@lru_cache(maxsize=1)
def load_champ_mapping() -> dict[str, str]:
    """讀取英雄顯示名 → DDragon ID 映射。"""
    if config.DD_META_PATH.exists():
        meta = json.loads(config.DD_META_PATH.read_text(encoding="utf-8"))
        return meta.get("name_to_id", {})
    return {}


def champ_id(name: str | None) -> str | None:
    if not name:
        return None
    mapping = load_champ_mapping()
    return mapping.get(name)


def champ_url(name: str | None) -> str | None:
    """回傳本機頭像 URL（由 Flask 路由提供，無須外網）。"""
    cid = champ_id(name)
    if not cid:
        return None
    return f"/champ-img/{quote(cid)}.png"


def champ_img(name: str | None, size: int = 26) -> html.Img:
    """英雄頭像元件；缺圖時以文字方塊代替。"""
    url = champ_url(name)
    if url:
        return html.Img(src=url, width=size, height=size,
                        className="champ-icon", title=name)
    return html.Span(
        (name or "?")[:2],
        style={"width": size, "height": size, "fontSize": size * 0.42,
               "display": "inline-flex", "alignItems": "center",
               "justifyContent": "center", "borderRadius": 6,
               "background": "#1b2740", "color": theme.COLORS["muted"]},
        title=name,
    )


def make_filter(data: dict | None, **overrides) -> data_access.Filter:
    """將 dcc.Store 的篩選字典轉成查詢層 Filter。"""
    data = data or {}
    return data_access.Filter(
        leagues=data.get("leagues") or None,
        years=[int(y) for y in data["years"]] if data.get("years") else None,
        patches=data.get("patches") or None,
        date_start=data.get("date_start") or None,
        date_end=data.get("date_end") or None,
        positions=data.get("positions") or None,
        champion=(data.get("champion") or "").strip() or None,
        member=(data.get("member") or "").strip() or None,
        **overrides,
    )


def kpi_card(label: str, value, sub: str = "", color: str = "text-gold") -> html.Div:
    return html.Div(
        html.Div([
            html.Div(label, className="kpi-label"),
            html.Div(str(value), className=f"kpi-value {color}"),
            html.Div(sub, className="kpi-sub") if sub else None,
        ]), className="kpi-card"
    )


def panel(title: str, children, tag: str | None = None,
          extra_class: str = "") -> html.Div:
    class_name = "panel" + (f" {extra_class}" if extra_class else "")
    return html.Div([
        html.H2(className="panel-title", children=[
            title, html.Span(tag, className="tag") if tag else None
        ]),
        children,
    ], className=class_name)


def empty_state(title: str, desc: str) -> html.Div:
    return html.Div([html.H3(title), html.P(desc)], className="empty-state")


def table_styles() -> dict:
    """DataTable 深色主題樣式。"""
    c = theme.COLORS
    return {
        "style_table": {"overflowX": "auto"},
        "style_cell": {
            "backgroundColor": c["panel_2"], "color": c["text"],
            "border": f"1px solid {c['border']}", "padding": "6px 10px",
            "textAlign": "center", "fontSize": 12, "minWidth": 60,
            "maxWidth": 220,
        },
        "style_header": {
            "backgroundColor": "#1a2540", "color": c["muted"],
            "fontWeight": 700, "border": f"1px solid {c['border']}",
        },
        "style_data_conditional": [
            {"if": {"row_index": "odd"},
             "backgroundColor": "rgba(255,255,255,0.02)"},
        ],
        "style_header_conditional": [
            {"if": {"column_id": "champion_col"}, "textAlign": "left"},
        ],
        "style_cell_conditional": [
            {"if": {"column_id": "champion_col"}, "textAlign": "left"},
        ],
    }


def champion_cell(name: str) -> html.Span:
    """表格內的英雄儲存格（頭像＋名稱）。"""
    return html.Span([champ_img(name, 24), html.Span(f" {name}")],
                     className="champ-cell")


def fmt_value(v, suffix: str = "", dash: str = "—") -> str:
    """空值顯示 —，數值加單位。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return dash
    return f"{v}{suffix}"
