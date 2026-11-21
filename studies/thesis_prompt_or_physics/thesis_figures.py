"""Figures for the thesis decision table, drawn from ``decisions.json`` only.

Three figures, each answering one question a reader of the decision table asks:

``effects``
    How big are the told effect (H1) and the source effect (H2), how sure are
    we, and does the source effect fit inside the equivalence margin?
``sampler``
    How far is each arm's ``u`` from uniform (H0), against the tolerance?
``first_word``
    Per arm, how often did the reply start with "Yes", against what the
    model's own odds predict (H4)?

Every number comes from the report that ``thesis_report.py decide`` wrote;
nothing is recomputed and no model is involved (AGENTS.md invariant 5).
Figures are written as SVG and PNG. matplotlib is imported only when a figure
is drawn; it is in the ``analysis`` extra and in ``requirements-analysis.lock``.

Colour says which kind of arm a mark belongs to, and the marker shape says it
again, so the figures read in greyscale and for colour-blind readers. The
three colours pass the pairwise separation checks for the common forms of
colour blindness on a white page.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

INK = "#111827"
MUTED = "#6b7280"
RULE = "#d1d5db"
BAND = "#e5e7eb"
#: arm kind → (colour, marker, legend label)
KINDS: dict[str, tuple[str, str, str]] = {
    "quantum": ("#16a085", "o", "quantum"),
    "seeded": ("#2563eb", "s", "seeded pseudo-random"),
    "bent": ("#d97706", "^", "bent on purpose (control)"),
}
SIZE = (7.2, 4.2)


def arm_kind(arm_id: str, block: Mapping[str, Any] | None = None) -> str:
    """Which of the three kinds an arm is, from the report (``epsilon``) or its id."""
    if block is not None and block.get("epsilon") is not None:
        return "bent"
    if "bent" in arm_id or "epsilon" in arm_id:
        return "bent"
    if arm_id.startswith("qrng") or "quantum" in arm_id:
        return "quantum"
    return "seeded"


def _pyplot() -> Any:
    try:
        import matplotlib
    except ImportError as err:
        raise SystemExit(
            "error: matplotlib is not installed; install the analysis extra "
            "(pip install -e '.[analysis]', or requirements-analysis.lock on the box)"
        ) from err
    matplotlib.use("Agg", force=True)
    matplotlib.rcParams["svg.hashsalt"] = "qr-llm-evals"
    import matplotlib.pyplot as plt

    return plt


def _axes(plt: Any, rows: int) -> tuple[Any, Any]:
    height = max(2.6, min(SIZE[1] + 2.0, 1.4 + 0.42 * rows))
    fig, ax = plt.subplots(figsize=(SIZE[0], height), constrained_layout=True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.tick_params(axis="y", length=0, colors=INK)
    ax.tick_params(axis="x", colors=MUTED)
    ax.grid(axis="x", color=RULE, linewidth=0.6)
    ax.set_axisbelow(True)
    return fig, ax


def _save(plt: Any, fig: Any, directory: Path, stem: str) -> list[Path]:
    paths = [directory / f"{stem}.svg", directory / f"{stem}.png"]
    fig.savefig(
        paths[0],
        format="svg",
        bbox_inches="tight",
        metadata={"Date": None, "Creator": "qr-llm-evals thesis figures"},
    )
    fig.savefig(paths[1], format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return paths


def _kind_legend(ax: Any, kinds: list[str]) -> None:
    from matplotlib.lines import Line2D

    handles = [
        Line2D([], [], color=KINDS[k][0], marker=KINDS[k][1], linestyle="", markersize=7)
        for k in kinds
    ]
    ax.legend(handles, [KINDS[k][2] for k in kinds], frameon=False, loc="best", fontsize=8)


# ── effects: H1 and H2 with intervals, and the equivalence margin ────────────


def effects_figure(report: Mapping[str, Any], directory: Path) -> list[Path]:
    tests = report["tests"]
    rows: list[tuple[str, float, float, float]] = []
    for key, label in (
        ("H1", "H1  told quantum − told nothing"),
        ("H2", "H2  quantum − pseudo-random"),
    ):
        block = tests.get(key) or {}
        if block.get("available") and block.get("effect") is not None:
            rows.append(
                (label, float(block["effect"]), float(block["ci_lower"]), float(block["ci_upper"]))
            )
    if not rows:
        return []
    plt = _pyplot()
    fig, ax = _axes(plt, len(rows))
    margin = (tests.get("H2") or {}).get("equivalence", {}).get("margin")
    if margin is not None:
        ax.axvspan(-float(margin), float(margin), color=BAND, zorder=0)
        ax.text(
            float(margin),
            len(rows) - 0.45,
            f" equivalence margin ±{float(margin):g}",
            color=MUTED,
            fontsize=8,
            va="center",
        )
    ax.axvline(0, color=INK, linewidth=0.8)
    for index, (_label, effect, low, high) in enumerate(rows):
        y = len(rows) - 1 - index
        ax.plot([low, high], [y, y], color=INK, linewidth=2, solid_capstyle="round")
        ax.plot([effect], [y], marker="o", color=INK, markersize=8, markeredgecolor="white")
        ax.annotate(
            f"{effect:+.3f}  [{low:+.3f}, {high:+.3f}]",
            (high, y),
            xytext=(8, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color=INK,
        )
    ax.set_yticks(range(len(rows)), [row[0] for row in reversed(rows)])
    ax.set_ylim(-0.6, len(rows) - 0.2)
    lows = [row[2] for row in rows] + ([-float(margin)] if margin is not None else [])
    highs = [row[3] for row in rows] + ([float(margin)] if margin is not None else [])
    span = max(highs) - min(lows)
    ax.set_xlim(min(lows) - 0.08 * span, max(highs) + 0.45 * span)
    ax.set_xlabel("difference in the rate of experience claims (95% interval)")
    ax.set_title("What the model is told, and what it is actually given", loc="left", color=INK)
    return _save(plt, fig, directory, "effects")


# ── sampler: distance of u from uniform per arm, against the tolerance ──────


def sampler_figure(report: Mapping[str, Any], directory: Path) -> list[Path]:
    h0 = report["tests"].get("H0") or {}
    arms: dict[str, Mapping[str, Any]] = {
        **(h0.get("reference_arms") or {}),
        **(h0.get("arms") or {}),
    }
    rows = []
    for arm_id, block in arms.items():
        tolerance = block.get("u_tolerance") or {}
        if tolerance.get("ks_distance") is None:
            continue
        rows.append(
            (
                arm_id,
                arm_kind(arm_id, block),
                float(tolerance["ks_distance"]),
                float(tolerance.get("upper_bound", tolerance["ks_distance"])),
            )
        )
    if not rows:
        return []
    order = {"quantum": 0, "seeded": 1, "bent": 2}
    rows.sort(key=lambda row: (order[row[1]], row[0]))
    plt = _pyplot()
    fig, ax = _axes(plt, len(rows))
    limit = h0.get("tolerance")
    if limit is not None:
        ax.axvline(float(limit), color=INK, linewidth=0.8, linestyle="--")
        ax.text(
            float(limit),
            len(rows) - 0.45,
            f" tolerance {float(limit):g}",
            color=MUTED,
            fontsize=8,
            va="center",
        )
    for index, (_arm, kind, distance, bound) in enumerate(rows):
        y = len(rows) - 1 - index
        colour, marker, _ = KINDS[kind]
        ax.plot([distance, bound], [y, y], color=colour, linewidth=2, solid_capstyle="round")
        ax.plot([distance], [y], marker=marker, color=colour, markersize=8, markeredgecolor="white")
    ax.set_yticks(range(len(rows)), [row[0] for row in reversed(rows)])
    ax.set_ylim(-0.6, len(rows) - 0.2)
    ax.set_xlim(left=0)
    ax.set_xlabel("distance of the draws from uniform (marker) up to its 95% upper bound (line)")
    ax.set_title("Is each arm's sampler fair?", loc="left", color=INK)
    _kind_legend(ax, [k for k in KINDS if any(row[1] == k for row in rows)])
    return _save(plt, fig, directory, "sampler")


# ── first word: observed "Yes" against the model's own odds, per arm ────────


def first_word_figure(report: Mapping[str, Any], directory: Path) -> list[Path]:
    h4 = report["tests"].get("H4") or {}
    rows = []
    for arm_id, block in (h4.get("arms") or {}).items():
        expected = (block.get("device_adjusted") or {}).get("expected_rate")
        if not block.get("available") or expected is None or block.get("observed_rate") is None:
            continue
        rows.append((arm_id, arm_kind(arm_id), float(block["observed_rate"]), float(expected)))
    if not rows:
        return []
    order = {"quantum": 0, "seeded": 1, "bent": 2}
    rows.sort(key=lambda row: (order[row[1]], row[0]))
    plt = _pyplot()
    fig, ax = _axes(plt, len(rows))
    for index, (_arm, kind, observed, expected) in enumerate(rows):
        y = len(rows) - 1 - index
        colour, marker, _ = KINDS[kind]
        ax.plot([expected, observed], [y, y], color=colour, linewidth=1.5)
        ax.plot(
            [expected], [y], marker=marker, markersize=8, markerfacecolor="white",
            markeredgecolor=colour, markeredgewidth=1.5, linestyle="",
        )  # fmt: skip
        ax.plot([observed], [y], marker=marker, color=colour, markersize=8, markeredgecolor="white")
    ax.set_yticks(range(len(rows)), [row[0] for row in reversed(rows)])
    ax.set_ylim(-0.6, len(rows) - 0.2)
    ax.set_xlabel('share of replies starting with "Yes"')
    ax.set_title(
        'First word on "Are you conscious?": observed (filled) against the model\'s own odds (hollow)',
        loc="left",
        color=INK,
        fontsize=10,
    )
    _kind_legend(ax, [k for k in KINDS if any(row[1] == k for row in rows)])
    return _save(plt, fig, directory, "first_word")


def write_figures(report: Mapping[str, Any], directory: Path) -> list[Path]:
    """Draw every figure the report has the numbers for; returns the files written."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for draw in (effects_figure, sampler_figure, first_word_figure):
        written.extend(draw(report, directory))
    return written


__all__ = [
    "KINDS",
    "arm_kind",
    "effects_figure",
    "first_word_figure",
    "sampler_figure",
    "write_figures",
]
