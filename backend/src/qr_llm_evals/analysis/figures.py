"""Script-only SVG figures using matplotlib's non-interactive Agg backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

PALETTE = {
    "source": "#16a085",
    "seed_vs_seed": "#6b7280",
    "epsilon": "#d97706",
    "supervised": "#7c3aed",
    "envelope": "#9ca3af",
}


def _matplotlib() -> Any:
    import matplotlib

    matplotlib.use("Agg", force=True)
    matplotlib.rcParams["svg.hashsalt"] = "qr-llm-evals"
    import matplotlib.pyplot as plt

    return plt


def _save(fig: Any, path: Path) -> None:
    fig.savefig(
        path,
        format="svg",
        bbox_inches="tight",
        metadata={"Date": None, "Creator": "qr-llm-evals analysis"},
    )


def _primary_figure(table: dict[str, Any], path: Path) -> None:
    plt = _matplotlib()
    comparisons = [
        item
        for item in table.get("comparisons", [])
        if item.get("kind") in {"source", "seed_vs_seed", "supervised"}
    ]
    fig, ax = plt.subplots(figsize=(7.2, 4.2), constrained_layout=True)
    labels = [
        "QRNG - pooled PRNG"
        if item["kind"] == "source"
        else " vs ".join((item["arms_a"][0], item["arms_b"][0]))
        for item in comparisons
    ]
    effects = [
        float(item.get("effect", 0.0)) if item.get("available") else 0.0 for item in comparisons
    ]
    colors = [PALETTE.get(str(item["kind"]), PALETTE["seed_vs_seed"]) for item in comparisons]
    if comparisons:
        bars = ax.bar(range(len(comparisons)), effects, color=colors)
        for bar, item in zip(bars, comparisons, strict=True):
            if not item.get("available"):
                bar.set_hatch("///")
                bar.set_alpha(0.35)
        ax.set_xticks(range(len(labels)), labels, rotation=25, ha="right")
    else:
        ax.text(0.5, 0.5, "Endpoint unavailable", ha="center", va="center", transform=ax.transAxes)
        ax.set_xticks([])
    ax.axhline(0, color="#111827", linewidth=0.8)
    ax.set_ylabel("Effect / test statistic")
    ax.set_title(f"{table['card_id']} - {table['endpoint']}\nTreatment with seed controls")
    _save(fig, path)
    plt.close(fig)


def _cumulative_figures(table: dict[str, Any], directory: Path, stem: str) -> list[Path]:
    plt = _matplotlib()
    special = table.get("special") or {}
    sessions = special.get("sessions") if isinstance(special, dict) else None
    if not isinstance(sessions, dict):
        return []
    paths: list[Path] = []
    for session_id, session in sorted(sessions.items()):
        cumulative = session.get("cumulative_deviation", {})
        values = cumulative.get("cumdev", [])
        # A study that registered its own interval level writes ``envelope`` with
        # ``envelope_level``; every other table carries ``envelope_95``.
        level = cumulative.get("envelope_level")
        if isinstance(level, int | float) and not isinstance(level, bool):
            envelope = cumulative.get("envelope", [])
            envelope_label = f"{round(100 * level, 6):g}% envelope"
        else:
            envelope = cumulative.get("envelope_95", [])
            envelope_label = "95% envelope"
        if not values:
            continue
        fig, ax = plt.subplots(figsize=(7.2, 4.2), constrained_layout=True)
        x = range(1, len(values) + 1)
        ax.plot(x, values, color=PALETTE["source"], label="cumulative deviation")
        ax.plot(x, envelope, color=PALETTE["envelope"], linestyle="--", label=envelope_label)
        ax.plot(x, [-float(value) for value in envelope], color=PALETTE["envelope"], linestyle="--")
        ax.axhline(0, color="#111827", linewidth=0.8)
        ax.set_xlabel("Second bin")
        ax.set_ylabel("Cumulative excess bits")
        ax.set_title(f"Cumulative deviation - session {session_id}")
        ax.legend(frameon=False)
        path = directory / f"{stem}--{session_id}.svg"
        _save(fig, path)
        plt.close(fig)
        paths.append(path)
    return paths


def write_figures(directory: Path, tables: dict[str, dict[str, Any]]) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, table in sorted(tables.items()):
        if table.get("role") == "primary":
            path = directory / f"{name}.svg"
            _primary_figure(table, path)
            written.append(path)
        if table.get("card_id") == "stream_deviation_windows":
            written.extend(_cumulative_figures(table, directory, name))
    return written


__all__ = ["PALETTE", "write_figures"]
