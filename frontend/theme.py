"""全站主題：色彩常數與 Plotly 深色模板。"""
from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

# 對應參考圖的深色電競風格
COLORS = {
    "bg": "#0B1020",
    "panel": "#131A2A",
    "panel_2": "#0E1526",
    "border": "#22304A",
    "text": "#E6ECF5",
    "muted": "#8A97AB",
    "blue": "#4C8DFF",
    "red": "#F05A6A",
    "gold": "#F5B945",
    "green": "#4CAF7A",
    "heat_hi": "#F05A6A",
    "heat_mid": "#F5B945",
    "heat_lo": "#4CAF7A",
}

_FONT = dict(family="Microsoft JhengHei, Noto Sans TC, sans-serif",
             color=COLORS["text"], size=13)


def build_plotly_template() -> go.layout.Template:
    """建立全站 Plotly 深色模板。"""
    tpl = go.layout.Template()
    tpl.layout = go.Layout(
        paper_bgcolor=COLORS["panel"],
        plot_bgcolor=COLORS["panel_2"],
        font=_FONT,
        title=dict(font=dict(size=16, color=COLORS["text"])),
        colorway=[COLORS["blue"], COLORS["red"], COLORS["gold"],
                  COLORS["green"], "#9D7BFF", "#3FC1C9"],
        xaxis=dict(gridcolor=COLORS["border"], zerolinecolor=COLORS["border"],
                   linecolor=COLORS["border"], tickfont=dict(size=11)),
        yaxis=dict(gridcolor=COLORS["border"], zerolinecolor=COLORS["border"],
                   linecolor=COLORS["border"], tickfont=dict(size=11)),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=12)),
        margin=dict(l=40, r=20, t=50, b=40),
    )
    return tpl


pio.templates["lol_dark"] = build_plotly_template()
PLOTLY_TEMPLATE = "lol_dark"


def heat_color(value: float, lo: float = 0, hi: float = 100) -> str:
    """依數值回傳熱力文字色（低綠、中金、高紅）。"""
    if value is None:
        return COLORS["muted"]
    if value >= hi * 0.66:
        return COLORS["heat_hi"]
    if value >= hi * 0.33:
        return COLORS["heat_mid"]
    return COLORS["heat_lo"]
