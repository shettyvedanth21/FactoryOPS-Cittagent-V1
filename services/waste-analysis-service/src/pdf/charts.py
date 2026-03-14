from __future__ import annotations

import base64
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


def _fig_to_data_uri(fig) -> str:
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _compact_style(ax) -> None:
    ax.set_facecolor("#ffffff")
    ax.tick_params(axis="both", labelsize=8, colors="#334155")
    ax.grid(color="#cbd5e1", alpha=0.35, linewidth=0.6)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#cbd5e1")
    ax.spines["bottom"].set_color("#cbd5e1")


def _truncate(label: str, max_len: int = 20) -> str:
    return label if len(label) <= max_len else label[: max_len - 1] + "…"


def _value_label_padding(max_value: float) -> float:
    if max_value <= 0:
        return 0.1
    return max(0.02 * max_value, 0.02)


def idle_cost_bar(rows: list[dict]) -> str | None:
    if not rows:
        return None
    labels = [_truncate(r.get("device_name") or r.get("device_id") or "Unknown") for r in rows]
    vals = [float(r.get("idle_cost") or 0.0) for r in rows]
    if not any(vals):
        return None
    h = min(3.2, max(2.0, 1.45 + 0.43 * len(labels)))
    fig, ax = plt.subplots(figsize=(7.0, h))
    bars = ax.barh(labels, vals, color="#2563eb", height=0.48, edgecolor="#1d4ed8")
    _compact_style(ax)
    ax.set_xlabel("Idle Cost", fontsize=8, color="#475569")
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.xaxis.grid(True)
    ax.yaxis.grid(False)
    ax.invert_yaxis()
    max_val = max(vals) if vals else 0
    ax.set_xlim(0, max_val * 1.18 if max_val > 0 else 1)
    pad = _value_label_padding(max_val)
    for b, v in zip(bars, vals):
        ax.text(
            b.get_width() + pad,
            b.get_y() + (b.get_height() / 2),
            f"₹{v:.2f}",
            va="center",
            ha="left",
            fontsize=8,
            color="#1e3a8a",
            fontweight="bold",
        )
    fig.tight_layout(pad=0.6)
    return _fig_to_data_uri(fig)


def total_energy_bar(rows: list[dict]) -> str | None:
    if not rows:
        return None
    labels = [_truncate(r.get("device_name") or r.get("device_id") or "Unknown", max_len=16) for r in rows]
    vals = [float(r.get("total_energy_kwh") or 0.0) for r in rows]
    if not any(vals):
        return None
    h = min(3.2, max(2.1, 1.7 + 0.2 * len(labels)))
    fig, ax = plt.subplots(figsize=(7.0, h))
    bars = ax.bar(labels, vals, color="#3b82f6", edgecolor="#2563eb", width=0.52)
    _compact_style(ax)
    ax.set_ylabel("kWh", fontsize=8, color="#475569")
    ax.tick_params(axis="x", rotation=18)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.yaxis.grid(True)
    ax.xaxis.grid(False)
    max_val = max(vals) if vals else 0
    ax.set_ylim(0, max_val * 1.22 if max_val > 0 else 1)
    for b, v in zip(bars, vals):
        ax.text(
            b.get_x() + (b.get_width() / 2),
            b.get_height() + _value_label_padding(max_val),
            f"{v:.2f}",
            ha="center",
            va="bottom",
            fontsize=7.5,
            color="#1e3a8a",
            fontweight="bold",
        )
    fig.tight_layout(pad=0.6)
    return _fig_to_data_uri(fig)


def standby_bar(rows: list[dict]) -> str | None:
    available = [r for r in rows if r.get("standby_power_kw") is not None]
    if not available:
        return None
    labels = [_truncate(r.get("device_name") or r.get("device_id") or "Unknown", max_len=16) for r in available]
    vals = [float(r.get("standby_power_kw") or 0.0) for r in available]
    if not any(v > 0 for v in vals):
        return None
    h = min(3.2, max(2.1, 1.7 + 0.2 * len(labels)))
    fig, ax = plt.subplots(figsize=(7.0, h))
    bars = ax.bar(labels, vals, color="#f59e0b", edgecolor="#d97706", width=0.52)
    _compact_style(ax)
    ax.set_ylabel("kW", fontsize=8, color="#475569")
    ax.set_title("Average Standby Power", fontsize=9, color="#334155", pad=8)
    ax.tick_params(axis="x", rotation=18)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.yaxis.grid(True)
    ax.xaxis.grid(False)
    max_val = max(vals) if vals else 0
    ax.set_ylim(0, max_val * 1.22 if max_val > 0 else 1)
    for b, v in zip(bars, vals):
        ax.text(
            b.get_x() + (b.get_width() / 2),
            b.get_height() + _value_label_padding(max_val),
            f"{v:.2f} kW",
            ha="center",
            va="bottom",
            fontsize=7.5,
            color="#9a3412",
            fontweight="bold",
        )
    fig.tight_layout(pad=0.6)
    return _fig_to_data_uri(fig)


def offhours_cost_bar(rows: list[dict]) -> str | None:
    if not rows:
        return None
    labels = [_truncate(r.get("device_name") or r.get("device_id") or "Unknown") for r in rows]
    vals = [float(r.get("offhours_cost") or 0.0) for r in rows]
    if not any(vals):
        return None
    h = min(3.2, max(2.0, 1.45 + 0.43 * len(labels)))
    fig, ax = plt.subplots(figsize=(7.0, h))
    bars = ax.barh(labels, vals, color="#0ea5e9", height=0.48, edgecolor="#0284c7")
    _compact_style(ax)
    ax.set_xlabel("Off-Hours Cost", fontsize=8, color="#475569")
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.xaxis.grid(True)
    ax.yaxis.grid(False)
    ax.invert_yaxis()
    max_val = max(vals) if vals else 0
    ax.set_xlim(0, max_val * 1.18 if max_val > 0 else 1)
    pad = _value_label_padding(max_val)
    for b, v in zip(bars, vals):
        ax.text(
            b.get_width() + pad,
            b.get_y() + (b.get_height() / 2),
            f"₹{v:.2f}",
            va="center",
            ha="left",
            fontsize=8,
            color="#0c4a6e",
            fontweight="bold",
        )
    fig.tight_layout(pad=0.6)
    return _fig_to_data_uri(fig)


def overconsumption_cost_bar(rows: list[dict]) -> str | None:
    if not rows:
        return None
    labels = [_truncate(r.get("device_name") or r.get("device_id") or "Unknown") for r in rows]
    vals = [float(r.get("overconsumption_cost") or 0.0) for r in rows]
    if not any(vals):
        return None
    h = min(3.2, max(2.0, 1.45 + 0.43 * len(labels)))
    fig, ax = plt.subplots(figsize=(7.0, h))
    bars = ax.barh(labels, vals, color="#ef4444", height=0.48, edgecolor="#dc2626")
    _compact_style(ax)
    ax.set_xlabel("Overconsumption Cost", fontsize=8, color="#475569")
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.xaxis.grid(True)
    ax.yaxis.grid(False)
    ax.invert_yaxis()
    max_val = max(vals) if vals else 0
    ax.set_xlim(0, max_val * 1.18 if max_val > 0 else 1)
    pad = _value_label_padding(max_val)
    for b, v in zip(bars, vals):
        ax.text(
            b.get_width() + pad,
            b.get_y() + (b.get_height() / 2),
            f"₹{v:.2f}",
            va="center",
            ha="left",
            fontsize=8,
            color="#7f1d1d",
            fontweight="bold",
        )
    fig.tight_layout(pad=0.6)
    return _fig_to_data_uri(fig)
