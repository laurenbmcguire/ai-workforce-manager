"""Single matplotlib style helper for every chart in the repo (brand palette)."""

from __future__ import annotations

import logging

import matplotlib

matplotlib.use("Agg")
# DejaVu Sans has no semibold face; silence the fallback-to-bold notice.
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
import matplotlib.pyplot as plt  # noqa: E402

BACKGROUND = "#FFFBF6"
INK = "#241A18"
MUTED = "#5E4C46"
GRID = "#EAE0D5"
CRIMSON = "#BE1E3C"
DEEP_CRIMSON = "#8E1630"
GOLD = "#B08A4E"
SAND = "#E3D2B4"

PALETTE = [CRIMSON, GOLD, DEEP_CRIMSON, SAND]
FIGSIZE = (10, 5.625)  # 1600 x 900 px at dpi=160
DPI = 160


def apply_style() -> None:
    """Apply the brand rcParams globally."""
    plt.rcParams.update(
        {
            "figure.facecolor": BACKGROUND,
            "axes.facecolor": BACKGROUND,
            "savefig.facecolor": BACKGROUND,
            "axes.edgecolor": MUTED,
            "axes.labelcolor": INK,
            "axes.titlecolor": INK,
            "axes.titlesize": 15,
            "axes.titleweight": "semibold",
            "axes.titlelocation": "left",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "axes.axisbelow": True,
            "axes.prop_cycle": matplotlib.cycler(color=PALETTE),
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "text.color": INK,
            "legend.frameon": False,
            "font.size": 10.5,
        }
    )


def new_figure(nrows: int = 1, ncols: int = 1, **kwargs):
    """Create a styled figure sized ~1600x900 px."""
    apply_style()
    fig, axes = plt.subplots(nrows, ncols, figsize=FIGSIZE, **kwargs)
    return fig, axes


def save(fig, path) -> None:
    """Save a figure at the brand DPI and close it."""
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
