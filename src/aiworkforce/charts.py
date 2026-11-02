"""Charts written to docs/img by the pipeline (brand style, 1600x900 px)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .policy import STRATEGY_LABELS
from .style import CRIMSON, DEEP_CRIMSON, GOLD, GRID, INK, MUTED, SAND, new_figure, save


def strategy_comparison(table: pd.DataFrame, best: str, path: Path) -> None:
    """Hero chart: cost per task vs first-pass quality vs SLA attainment by strategy."""
    fig, axes = new_figure(1, 3)
    labels = [STRATEGY_LABELS[s].replace(" skill-based", "\nskill-based") for s in table["strategy"]]
    colors = [CRIMSON if s == best else SAND for s in table["strategy"]]
    panels = [
        ("cost_per_task", "Cost per task (USD)", lambda v: f"${v:.2f}", False),
        ("first_pass_quality", "First-pass quality", lambda v: f"{100 * v:.1f}%", True),
        ("sla_attainment", "SLA attainment", lambda v: f"{100 * v:.1f}%", True),
    ]
    for ax, (col, title, fmt, pct) in zip(axes, panels):
        vals = table[col].to_numpy(dtype=float)
        shown = vals * 100 if pct else vals
        bars = ax.bar(labels, shown, color=colors, edgecolor=DEEP_CRIMSON, linewidth=0.6)
        for b, v in zip(bars, vals):
            ax.annotate(fmt(v), (b.get_x() + b.get_width() / 2, b.get_height()),
                        xytext=(0, 4), textcoords="offset points", ha="center",
                        fontsize=10, color=INK, fontweight="semibold")
        ax.set_title(title, fontsize=12.5)
        if pct:
            lo = max(0.0, np.floor(shown.min() / 10) * 10 - 10)
            ax.set_ylim(lo, 104)
        else:
            ax.set_ylim(0, shown.max() * 1.18)
        ax.tick_params(axis="x", labelsize=9.5)
        ax.grid(axis="x", visible=False)
    fig.suptitle("Routing strategy comparison: cost vs quality vs SLA", x=0.012, ha="left",
                 fontsize=15, fontweight="semibold", color=INK)
    net = {r.strategy: r.net_value for r in table.itertuples()}
    fig.text(0.012, 0.015,
             f"Same 8-week synthetic task stream and random draws for all three. Highlighted: "
             f"{STRATEGY_LABELS[best]} (highest net value, ${net[best]:,.0f}).",
             fontsize=9.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))
    save(fig, path)


def agent_scorecard(akpi: pd.DataFrame, path: Path) -> None:
    """Per-agent scorecard: first-pass quality, SLA attainment, escalation and incident rates."""
    df = akpi[akpi["tasks"] > 0].sort_values("first_pass_quality")
    fig, ax = new_figure()
    y = np.arange(len(df))
    h = 0.2
    series = [
        ("first_pass_quality", "First-pass quality", CRIMSON),
        ("sla_attainment", "SLA attainment", GOLD),
        ("escalation_rate", "Escalation rate", DEEP_CRIMSON),
        ("incident_rate", "Incident rate", SAND),
    ]
    for k, (col, label, color) in enumerate(series):
        ax.barh(y + (1.5 - k) * h, df[col] * 100, height=h, color=color, label=label,
                edgecolor=DEEP_CRIMSON if color == SAND else color, linewidth=0.4)
    ax.set_yticks(y)
    ax.set_yticklabels([f"{n}  ({t}, L{a})" for n, t, a in
                        zip(df["name"], df["model_tier"], df["autonomy"])])
    ax.set_xlim(0, 105)
    ax.set_xlabel("%")
    ax.grid(axis="y", visible=False)
    ax.set_title("AI agent scorecard (deployed routing strategy)")
    ax.legend(ncol=4, loc="lower center", bbox_to_anchor=(0.5, -0.2), fontsize=9.5)
    fig.tight_layout()
    save(fig, path)


def weekly_throughput(weekly: pd.DataFrame, path: Path) -> None:
    fig, ax = new_figure()
    wk = weekly[weekly["completed"] > 0]
    ax.bar(wk["week"], wk["completed"], color=SAND, edgecolor=GOLD, label="Tasks completed")
    ax.set_xlabel("Week (by completion; queue drain after day 40 folded into the last week)")
    ax.set_ylabel("Tasks completed")
    ax.set_xticks(wk["week"])
    ax2 = ax.twinx()
    ax2.plot(wk["week"], wk["incidents"], color=CRIMSON, marker="o", linewidth=2.2,
             label="Incidents")
    ax2.set_ylabel("Incidents", color=CRIMSON)
    ax2.tick_params(axis="y", colors=CRIMSON)
    ax2.spines["right"].set_visible(True)
    ax2.spines["right"].set_color(CRIMSON)
    ax2.spines["top"].set_visible(False)
    ax2.grid(False)
    ax2.set_ylim(0, max(1, wk["incidents"].max()) * 1.6)
    for x, v in zip(wk["week"], wk["incidents"]):
        ax2.annotate(str(int(v)), (x, v), xytext=(0, 7), textcoords="offset points",
                     ha="center", color=DEEP_CRIMSON, fontsize=9.5)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left")
    ax.set_title("Weekly throughput and incidents")
    fig.tight_layout()
    save(fig, path)


def review_load(weekly_by_strategy: dict[str, pd.DataFrame], deployed: str, path: Path) -> None:
    """Human review hours worked per week (deployed strategy, stacked) vs capacity."""
    fig, ax = new_figure()
    wk = weekly_by_strategy[deployed]
    wk = wk[wk["capacity_h"] > 0]
    x = wk["week"].to_numpy()
    b1 = wk["approval_h"].to_numpy()
    b2 = wk["escalation_h"].to_numpy()
    b3 = wk["audit_h"].to_numpy()
    ax.bar(x, b1, color=CRIMSON, label="Approvals + rework")
    ax.bar(x, b2, bottom=b1, color=SAND, edgecolor=GOLD, linewidth=0.5, label="Escalations")
    ax.bar(x, b3, bottom=b1 + b2, color=DEEP_CRIMSON, label="Audit samples")
    ax.step(np.r_[x - 0.5, x[-1] + 0.5], np.r_[wk["capacity_h"].to_numpy(), wk["capacity_h"].iloc[-1]],
            where="post", color=INK, linewidth=1.8, label="Supervisor capacity")
    styles = {"round_robin": (DEEP_CRIMSON, "--"), "skill_based": (GOLD, ":"),
              "cost_aware": (CRIMSON, "-.")}
    for name, w in weekly_by_strategy.items():
        if name == deployed:
            continue
        w = w[w["capacity_h"] > 0]
        tot = w["approval_h"] + w["escalation_h"] + w["audit_h"]
        color, ls = styles.get(name, (MUTED, "--"))
        ax.plot(w["week"], tot, color=color, linestyle=ls, linewidth=2.2, marker="o", markersize=4,
                label=f"{STRATEGY_LABELS[name]} (total)")
    ax.set_xticks(x)
    ax.set_xlabel("Week")
    ax.set_ylabel("Reviewer hours")
    ax.set_title(f"Human review load vs capacity ({STRATEGY_LABELS[deployed]})")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper left", ncol=2, fontsize=9)
    ymax = max(wk["capacity_h"].max(), (b1 + b2 + b3).max())
    for w in weekly_by_strategy.values():
        ymax = max(ymax, (w["approval_h"] + w["escalation_h"] + w["audit_h"]).max())
    ax.set_ylim(0, ymax * 1.35)
    ax.axhline(0, color=GRID)
    fig.tight_layout()
    save(fig, path)
