"""
Matplotlib 可视化模块。

绘制合并K线 + 分型 + 笔，标注已确认/暂定/候选状态。
"""

from __future__ import annotations

import io
from typing import Any, Optional

import matplotlib
matplotlib.use("Agg")  # 非交互后端
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.dates import DateFormatter, AutoDateLocator


# 颜色方案
COLORS = {
    "up_candle": "#e74c3c",         # 红：阳线
    "down_candle": "#2ecc71",       # 绿：阴线
    "top_fractal": "#e74c3c",       # 红：顶分型
    "bottom_fractal": "#2ecc71",    # 绿：底分型
    "confirmed_stroke": "#3498db",  # 蓝：已确认笔
    "provisional_stroke": "#f39c12",# 橙：暂定笔
    "background": "#1a1a2e",        # 深色背景
    "grid": "#2d2d44",              # 网格线
    "text": "#cccccc",              # 文字
    "price_line": "#ffffff",        # 价格线
}


def plot_chan_structure(
    output: dict[str, Any],
    title: str = "Chan Structure Parser",
    figsize: tuple = (20, 10),
    dark_mode: bool = True,
    save_path: Optional[str] = None,
) -> plt.Figure:
    """绘制缠论结构图。

    Args:
        output: 结构分析输出字典
        title: 图表标题
        figsize: 图表尺寸
        dark_mode: 是否深色模式
        save_path: 保存路径（None 则不保存）

    Returns:
        matplotlib Figure 对象
    """
    structures = output.get("structures", {})
    merged_bars = structures.get("merged_bars", [])
    fractals = structures.get("fractals", [])
    strokes = structures.get("strokes", [])

    if not merged_bars:
        fig, ax = plt.subplots(figsize=figsize)
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
        return fig

    # 设置样式
    if dark_mode:
        plt.style.use("dark_background")
        bg_color = COLORS["background"]
        text_color = COLORS["text"]
    else:
        bg_color = "white"
        text_color = "black"

    fig, ax = plt.subplots(figsize=figsize)
    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(bg_color)

    # 准备数据
    indices = list(range(len(merged_bars)))
    closes = [mb["close"] for mb in merged_bars]
    highs = [mb["high"] for mb in merged_bars]
    lows = [mb["low"] for mb in merged_bars]
    opens = [mb["open"] for mb in merged_bars]

    # 绘制 K 线
    for i, mb in enumerate(merged_bars):
        color = COLORS["up_candle"] if mb["close"] >= mb["open"] else COLORS["down_candle"]
        # 影线
        ax.plot([i, i], [mb["low"], mb["high"]], color=color, linewidth=0.8)
        # 实体
        body_bottom = min(mb["open"], mb["close"])
        body_height = abs(mb["close"] - mb["open"])
        ax.bar(i, body_height, bottom=body_bottom, color=color, width=0.6, alpha=0.9)

    # 绘制已确认笔
    for s in strokes:
        if s["status"] == "CONFIRMED":
            color = COLORS["confirmed_stroke"]
            alpha = 0.8
            linewidth = 2.0
            linestyle = "-"
        elif s["status"] == "PROVISIONAL":
            color = COLORS["provisional_stroke"]
            alpha = 0.6
            linewidth = 1.5
            linestyle = "--"
        else:
            color = COLORS["provisional_stroke"]
            alpha = 0.4
            linewidth = 1.0
            linestyle = ":"

        x1, y1 = s["start_bar_index"], s["start_price"]
        x2, y2 = s["end_bar_index"], s["end_price"]
        ax.plot([x1, x2], [y1, y2], color=color, linewidth=linewidth,
                linestyle=linestyle, alpha=alpha, zorder=5)

    # 绘制分型
    for f in fractals:
        idx = f["merged_bar_index"]
        price = f["price"]
        if f["fractal_type"] == "TOP":
            color = COLORS["top_fractal"]
            marker = "v"
            offset = 2
        else:
            color = COLORS["bottom_fractal"]
            marker = "^"
            offset = -2

        if f["status"] == "CONFIRMED":
            size = 80
            alpha = 0.9
        elif f["status"] == "PROVISIONAL":
            size = 60
            alpha = 0.6
        else:
            size = 40
            alpha = 0.4

        # 在K线上方/下方标记
        label_y = price + offset * (max(highs) - min(lows)) * 0.02
        ax.scatter(idx, label_y, marker=marker, s=size, color=color,
                   alpha=alpha, zorder=10, edgecolors="white", linewidths=0.5)

        # 标注分型ID
        if f["status"] in ("CONFIRMED", "PROVISIONAL"):
            ax.annotate(
                f["fractal_id"].replace("fx_", ""),
                (idx, label_y),
                textcoords="offset points",
                xytext=(0, 8 if f["fractal_type"] == "TOP" else -12),
                ha="center",
                fontsize=6,
                color=color,
                alpha=0.8,
            )

    # 图例
    legend_elements = [
        mpatches.Patch(color=COLORS["up_candle"], label="Up Candle (Close>=Open)"),
        mpatches.Patch(color=COLORS["down_candle"], label="Down Candle (Close<Open)"),
        plt.Line2D([0], [0], color=COLORS["confirmed_stroke"], linewidth=2,
                    label="Confirmed Stroke"),
        plt.Line2D([0], [0], color=COLORS["provisional_stroke"], linewidth=1.5,
                    linestyle="--", label="Provisional Stroke"),
        plt.Line2D([0], [0], marker="v", color="w", markerfacecolor=COLORS["top_fractal"],
                    markersize=8, label="Top Fractal"),
        plt.Line2D([0], [0], marker="^", color="w", markerfacecolor=COLORS["bottom_fractal"],
                    markersize=8, label="Bottom Fractal"),
    ]
    ax.legend(handles=legend_elements, loc="upper left", fontsize=8,
              facecolor=bg_color, edgecolor=COLORS["grid"])

    # 标注和标题
    meta = output.get("meta", {})
    ax.set_title(
        f"{title}\n"
        f"Profile: {meta.get('profile_id', 'N/A')} | "
        f"Bars: {len(merged_bars)} | "
        f"Fractals: {len(fractals)} | "
        f"Strokes: {len(strokes)}",
        fontsize=11,
        color=text_color,
    )
    ax.set_xlabel("Merged Bar Index", fontsize=9, color=text_color)
    ax.set_ylabel("Price", fontsize=9, color=text_color)
    ax.tick_params(colors=text_color, labelsize=8)
    ax.grid(True, alpha=0.15, color=COLORS["grid"])

    plt.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight",
                    facecolor=bg_color, edgecolor="none")
        print(f"Chart saved to: {save_path}")

    return fig


def plot_to_bytes(output: dict[str, Any], figsize: tuple = (20, 10)) -> bytes:
    """生成图表并返回 PNG 字节流。"""
    fig = plot_chan_structure(output, figsize=figsize, dark_mode=True)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=COLORS["background"])
    plt.close(fig)
    buf.seek(0)
    return buf.read()
