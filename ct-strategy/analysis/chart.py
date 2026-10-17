"""캔들차트 이미지 생성 — mplfinance 기반."""

from __future__ import annotations

import io
from typing import Any

import matplotlib
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

matplotlib.use("Agg")  # GUI 없는 서버 환경

# 한글 폰트 설정 (Noto Sans CJK)
_KO_FONT = None
_KO_FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]
for _fp in _KO_FONT_PATHS:
    if __import__("os").path.exists(_fp):
        fm.fontManager.addfont(_fp)
        _KO_FONT = fm.FontProperties(fname=_fp)
        matplotlib.rcParams["font.family"] = _KO_FONT.get_name()
        matplotlib.rcParams["axes.unicode_minus"] = False
        break

# ── 패턴별 이상적 형태 좌표 ──
_PATTERN_SHAPES: dict[str, dict[str, Any]] = {
    "double_top": {
        "x": [0, 1, 2, 3, 4, 5, 6],
        "y": [3, 5, 3.5, 5, 3, 2, 1],
        "peaks": [1, 3],
        "label": "쌍봉",
        "arrow": "down",
    },
    "double_bottom": {
        "x": [0, 1, 2, 3, 4, 5, 6],
        "y": [5, 2, 3.5, 2, 3.5, 4.5, 5.5],
        "peaks": [],
        "valleys": [1, 3],
        "label": "쌍바닥",
        "arrow": "up",
    },
    "triple_top": {
        "x": [0, 1, 2, 3, 4, 5, 6, 7, 8],
        "y": [2, 5, 3, 5, 3, 5, 3, 1.5, 1],
        "peaks": [1, 3, 5],
        "label": "삼중천정",
        "arrow": "down",
    },
    "triple_bottom": {
        "x": [0, 1, 2, 3, 4, 5, 6, 7, 8],
        "y": [5, 2, 3.5, 2, 3.5, 2, 3.5, 5, 5.5],
        "peaks": [],
        "valleys": [1, 3, 5],
        "label": "트리플바닥",
        "arrow": "up",
    },
    "head_and_shoulders": {
        "x": [0, 1, 2, 3, 4, 5, 6, 7, 8],
        "y": [2, 4, 2.5, 5.5, 2.5, 4, 2.5, 1.5, 1],
        "peaks": [1, 3, 5],
        "label": "머리어깨",
        "arrow": "down",
    },
    "inverse_head_and_shoulders": {
        "x": [0, 1, 2, 3, 4, 5, 6, 7, 8],
        "y": [5, 3, 4, 1.5, 4, 3, 4, 5, 5.5],
        "peaks": [],
        "valleys": [1, 3, 5],
        "label": "역머리어깨",
        "arrow": "up",
    },
    "ascending_triangle": {
        "x": [0, 1, 2, 3, 4, 5, 6, 7],
        "y": [2, 5, 2.5, 5, 3, 5, 3.5, 5.5],
        "hline": 5,
        "label": "상승삼각형",
        "arrow": "up",
    },
    "descending_triangle": {
        "x": [0, 1, 2, 3, 4, 5, 6, 7],
        "y": [5, 2, 4.5, 2, 4, 2, 3.5, 1.5],
        "hline": 2,
        "label": "하락삼각형",
        "arrow": "down",
    },
    "bull_flag": {
        "x": [0, 1, 2, 3, 4, 5, 6, 7, 8],
        "y": [1, 2, 3.5, 5, 4.8, 4.5, 4.3, 4.5, 5.5],
        "label": "상승깃발",
        "arrow": "up",
    },
    "bear_flag": {
        "x": [0, 1, 2, 3, 4, 5, 6, 7, 8],
        "y": [5.5, 4.5, 3, 1.5, 1.8, 2, 2.2, 2, 1],
        "label": "하락깃발",
        "arrow": "down",
    },
    "rising_wedge": {
        "x": [0, 1, 2, 3, 4, 5, 6],
        "y": [1.5, 4, 2, 4.5, 2.5, 4.8, 2],
        "label": "상승쐐기형",
        "arrow": "down",
    },
    "falling_wedge": {
        "x": [0, 1, 2, 3, 4, 5, 6],
        "y": [5, 2.5, 4.5, 2.8, 4, 3, 5],
        "label": "하락쐐기형",
        "arrow": "up",
    },
}


def _draw_pattern_inset(
    fig: plt.Figure,
    pattern_name: str,
    pattern_ko: str,
    confidence: int,
    signal: str,
    position: int = 0,
) -> None:
    """fig 위에 패턴 형태를 작은 inset으로 그린다."""
    shape = _PATTERN_SHAPES.get(pattern_name)
    if shape is None:
        return

    # inset 위치 (하단 패턴 영역, 좌→우로 배치)
    inset_w, inset_h = 0.20, 0.12
    inset_x = 0.08 + position * (inset_w + 0.05)
    inset_y = 0.02

    if inset_x + inset_w > 0.95:
        return

    ax_inset = fig.add_axes(
        [inset_x, inset_y, inset_w, inset_h],
        facecolor="#2a2a2a",
    )

    x = shape["x"]
    y = shape["y"]
    color = "#26a69a" if signal == "BUY" else "#ef5350"

    ax_inset.plot(x, y, color=color, linewidth=2.0)
    ax_inset.fill_between(x, y, min(y) - 0.3, alpha=0.15, color=color)

    # 고점/저점 마킹
    for pi in shape.get("peaks", []):
        ax_inset.scatter(x[pi], y[pi], color="#ef5350", s=30, zorder=5)
    for vi in shape.get("valleys", []):
        ax_inset.scatter(x[vi], y[vi], color="#26a69a", s=30, zorder=5)

    # 수평선 (삼각형)
    if "hline" in shape:
        ax_inset.axhline(
            shape["hline"], color="#888888", linestyle="--", linewidth=0.8,
        )

    # 방향 화살표
    arrow_x = x[-1]
    arrow_y = y[-1]
    dy = 0.8 if shape["arrow"] == "up" else -0.8
    ax_inset.annotate(
        "",
        xy=(arrow_x + 0.3, arrow_y + dy),
        xytext=(arrow_x + 0.3, arrow_y),
        arrowprops={"arrowstyle": "->", "color": color, "lw": 2},
    )

    # 라벨
    title_text = f"{pattern_ko} ({confidence}%)"
    title_kwargs: dict[str, Any] = {
        "fontsize": 8,
        "color": color,
        "fontweight": "bold",
    }
    if _KO_FONT:
        title_kwargs["fontproperties"] = _KO_FONT
        title_kwargs["fontsize"] = 9
    ax_inset.set_title(title_text, pad=3, **title_kwargs)

    ax_inset.set_xlim(min(x) - 0.5, max(x) + 1.5)
    ax_inset.set_ylim(min(y) - 0.8, max(y) + 0.8)
    ax_inset.set_xticks([])
    ax_inset.set_yticks([])
    for spine in ax_inset.spines.values():
        spine.set_color("#555555")
        spine.set_linewidth(0.5)


def generate_chart(
    candles: list[dict[str, Any]],
    *,
    market: str = "",
    signal_type: str = "",
    support_levels: list[float] | None = None,
    resistance_levels: list[float] | None = None,
    patterns: list[dict[str, Any]] | None = None,
    resistance_trendline: dict[str, Any] | None = None,
    support_trendline: dict[str, Any] | None = None,
) -> bytes:
    """캔들 데이터로 차트 이미지(PNG bytes) 생성."""
    # DataFrame 구성
    df = pd.DataFrame(candles)
    df = df.rename(columns={
        "open_at": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    })
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")
    for col in ("Open", "High", "Low", "Close", "Volume"):
        df[col] = df[col].astype(float)

    # 스타일
    mc = mpf.make_marketcolors(
        up="#26a69a", down="#ef5350",
        edge="inherit",
        wick="inherit",
        volume="in",
    )
    style_kwargs: dict[str, Any] = {
        "base_mpf_style": "nightclouds",
        "marketcolors": mc,
        "figcolor": "#1e1e1e",
        "facecolor": "#1e1e1e",
        "gridcolor": "#333333",
        "gridstyle": "--",
        "y_on_right": True,
    }
    if _KO_FONT:
        style_kwargs["rc"] = {"font.family": _KO_FONT.get_name()}
    style = mpf.make_mpf_style(**style_kwargs)

    # 지지/저항 수평선
    hlines_dict: dict[str, Any] = {}
    hline_prices: list[float] = []
    hline_colors: list[str] = []

    if support_levels:
        for s in support_levels[:3]:
            hline_prices.append(s)
            hline_colors.append("#26a69a")
    if resistance_levels:
        for r in resistance_levels[:3]:
            hline_prices.append(r)
            hline_colors.append("#ef5350")

    if hline_prices:
        hlines_dict = {
            "hlines": hline_prices,
            "colors": hline_colors,
            "linestyle": "--",
            "linewidths": 1.0,
        }

    # 제목
    title = f"{market}  {signal_type}" if market else ""

    # 차트 생성
    chart_kwargs: dict[str, Any] = {
        "type": "candle",
        "style": style,
        "volume": True,
        "figsize": (14, 10),
        "title": title,
        "warn_too_much_data": 500,
        "returnfig": True,
    }
    if hlines_dict:
        chart_kwargs["hlines"] = hlines_dict
    if len(df) >= 20:
        chart_kwargs["mav"] = (20,) if len(df) < 50 else (20, 50)

    fig, axes = mpf.plot(df, **chart_kwargs)
    ax_main = axes[0]

    # 지지/저항 라벨
    if support_levels:
        for s in support_levels[:2]:
            ax_main.text(
                len(df) - 1, s, f"  S {s:,.0f}",
                fontsize=8, color="#26a69a", va="center",
            )
    if resistance_levels:
        for r in resistance_levels[:2]:
            ax_main.text(
                len(df) - 1, r, f"  R {r:,.0f}",
                fontsize=8, color="#ef5350", va="center",
            )

    # 추세선 (대각선)
    for tl, color, label in [
        (resistance_trendline, "#ef5350", "저항"),
        (support_trendline, "#26a69a", "지지"),
    ]:
        if tl:
            s_idx = tl["start_idx"]
            e_idx = min(tl["end_idx"], len(df) - 1)
            s_price = tl["start_price"]
            e_price = tl["end_price"]
            # 추세선을 차트 우측 끝까지 연장
            if e_idx > s_idx:
                slope = (e_price - s_price) / (e_idx - s_idx)
                ext_idx = len(df) - 1
                ext_price = s_price + slope * (ext_idx - s_idx)
                ax_main.plot(
                    [s_idx, ext_idx], [s_price, ext_price],
                    color=color, linestyle="--", linewidth=1.5, alpha=0.8,
                )
                ax_main.text(
                    ext_idx, ext_price, f"  {label}",
                    fontsize=8, color=color, va="center",
                )

    # 시그널 표시 (마지막 봉에 화살표)
    if signal_type in ("BUY", "SELL"):
        last_idx = len(df) - 1
        last_close = float(df["Close"].iloc[-1])
        arrow_color = "#26a69a" if signal_type == "BUY" else "#ef5350"
        marker = "^" if signal_type == "BUY" else "v"
        ax_main.scatter(
            last_idx, last_close,
            marker=marker, s=200, color=arrow_color, zorder=10,
        )

    # 패턴 형태 inset 그래프 추가 (하단 영역)
    if patterns:
        fig.subplots_adjust(bottom=0.18)
        for i, pat in enumerate(patterns[:3]):
            _draw_pattern_inset(
                fig,
                pattern_name=pat.get("name", ""),
                pattern_ko=pat.get("name_ko", ""),
                confidence=pat.get("confidence", 0),
                signal=pat.get("signal", "HOLD"),
                position=i,
            )

    # PNG로 변환
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def generate_chart_from_candle_dicts(
    candle_dicts: list[dict[str, Any]],
    *,
    market: str = "",
    signal_type: str = "",
    sr_analysis: dict[str, Any] | None = None,
    pattern_analysis: dict[str, Any] | None = None,
) -> bytes:
    """API 응답 dict 리스트에서 차트 이미지 생성."""
    return _chart_from_dicts(
        candle_dicts, market=market, signal_type=signal_type,
        sr_analysis=sr_analysis, pattern_analysis=pattern_analysis,
    )


def generate_chart_from_db_candles(
    candles: list,
    *,
    market: str = "",
    signal_type: str = "",
    sr_analysis: dict[str, Any] | None = None,
    pattern_analysis: dict[str, Any] | None = None,
) -> bytes:
    """DB Candle ORM 객체 리스트에서 차트 이미지 생성."""
    candle_dicts = [
        {
            "open_at": c.open_at,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume),
        }
        for c in candles
    ]
    return _chart_from_dicts(
        candle_dicts, market=market, signal_type=signal_type,
        sr_analysis=sr_analysis, pattern_analysis=pattern_analysis,
    )


def _chart_from_dicts(
    candle_dicts: list[dict[str, Any]],
    *,
    market: str = "",
    signal_type: str = "",
    sr_analysis: dict[str, Any] | None = None,
    pattern_analysis: dict[str, Any] | None = None,
) -> bytes:
    """dict 리스트 -> 차트 이미지 bytes."""

    support_levels = []
    resistance_levels = []
    if sr_analysis:
        for lv in sr_analysis.get("levels", []):
            if lv["type"] == "support":
                support_levels.append(lv["price"])
            else:
                resistance_levels.append(lv["price"])

    patterns = []
    resistance_trendline = None
    support_trendline = None
    if pattern_analysis:
        patterns = pattern_analysis.get("patterns", [])
        resistance_trendline = pattern_analysis.get("resistance_trendline")
        support_trendline = pattern_analysis.get("support_trendline")

    return generate_chart(
        candle_dicts,
        market=market,
        signal_type=signal_type,
        support_levels=support_levels or None,
        resistance_levels=resistance_levels or None,
        patterns=patterns or None,
        resistance_trendline=resistance_trendline,
        support_trendline=support_trendline,
    )
