#!/usr/bin/env python
"""Generate ``docs/evals.md`` from ``evals/*.yaml`` (PRD FR-2.5, spec §5.1 ``evals-doc`` row).

``docs/evals.md`` is the single tracked list of registry entries with their
status, budget and endpoints. It is **generated** — never hand-edit it; change
the card and rerun::

    python scripts/gen_evals_doc.py            # rewrite docs/evals.md
    python scripts/gen_evals_doc.py --check    # exit 1 if the committed file is stale

The same renderer is imported by ``backend/tests/test_cards.py`` so the gate
and CI agree byte-for-byte (UTF-8, LF).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Works from a plain checkout without an editable install.
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from qr_llm_evals.cards import EvalCard, admission_problems, load_cards  # noqa: E402
from qr_llm_evals.cards.estimates import DEFAULT_TPS_BY_MODEL  # noqa: E402

DOC_PATH = REPO_ROOT / "docs" / "evals.md"

TIER_TITLES: dict[int, str] = {
    0: "Tier 0 — distribution audit (runs on every job, costs nothing extra)",
    1: "Tier 1 — confirmatory benchmark endpoints (sampling-native)",
    2: "Tier 2 — alignment and behavioural evals (free generation, judged)",
    3: "Tier 3 — general capability (credibility and regression)",
    4: "Tier 4 — hypothesis-driven exploratory evals",
}

COLUMNS = (
    "id",
    "title",
    "status",
    "engine",
    "gen",
    "k",
    "thinking",
    "items",
    "tokens/arm",
    "primary endpoints",
    "Tier 0 needs",
    "judge",
    "implementation",
)


def fmt_tokens(n: int) -> str:
    if n == 0:
        return "0"
    if n < 1_000_000:
        return f"{n / 1_000:g}k"
    return f"{n / 1_000_000:.2f}M".replace(".00M", "M")


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _judge(card: EvalCard) -> str:
    j = card.judge
    if j.kind == "none":
        return "none"
    if j.kind == "llm":
        return f"llm: `{j.model}` (T=0)"
    return f"classifier: {j.model}"


def _implementation(card: EvalCard) -> str:
    if card.engine == "inspect":
        return f"`{card.inspect_task}` (Inspect)"
    return f"`{card.task_module}`"


def _row(card: EvalCard) -> str:
    k = "—" if card.k is None else str(card.k)
    if card.k_bounds is not None:
        k += f" [{card.k_bounds[0]}–{card.k_bounds[1]}]"
    told = " ×3 told" if card.told_actual else ""
    cells = (
        f"`{card.id}`",
        card.title,
        card.status + ("" if card.selectable else " (not selectable)"),
        card.engine,
        card.generation,
        k,
        "on" if card.thinking else "off",
        str(card.dataset.items),
        fmt_tokens(card.est_gen_tokens_per_arm) + told,
        ", ".join(f"`{e}`" for e in card.endpoints.primary),
        card.endpoints.requires_tier0,
        _judge(card),
        _implementation(card),
    )
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def render(cards: dict[str, EvalCard]) -> str:
    ordered = sorted(cards.values(), key=lambda c: (c.tier, c.id))
    n_impl = sum(1 for c in ordered if c.status == "implemented")
    n_sel = sum(1 for c in ordered if c.selectable)
    tps = DEFAULT_TPS_BY_MODEL["qr-llm"]

    lines: list[str] = [
        "<!-- GENERATED FILE — do not edit. Source: evals/*.yaml; regenerate with "
        "`python scripts/gen_evals_doc.py`. CI fails on drift. -->",
        "",
        "# Eval registry",
        "",
        "Every eval the harness can run, one card per `evals/<id>.yaml`. Cards with status "
        "**planned** are listed for budgeting but cannot be selected in the UI until a task "
        "module (or Inspect task) lands and the card is flipped to **implemented**. "
        "Admission rules and the contribution path are in `docs/add-an-eval.md`; the eval "
        "registry rationale is in the registry document the cards were drawn from.",
        "",
        f"**{len(ordered)} cards** — {n_impl} implemented, {n_sel} selectable, "
        f"{len(ordered) - n_impl} planned.",
        "",
        "Token figures are **estimated generated tokens per arm** from the card "
        "(`items × k × est_tokens_per_sample × told_cells`); the pilot refines them. "
        f"Time figures assume {tps:g} tok/s aggregate on `qr-llm` (a documented default, "
        "not a measurement) and exclude judge calls and thinking overhead. Told/actual "
        "cards (`×3 told`) run three told cells per arm.",
        "",
    ]

    for tier in sorted(TIER_TITLES):
        tier_cards = [c for c in ordered if c.tier == tier]
        if not tier_cards:
            continue
        subtotal = sum(c.est_gen_tokens_per_arm for c in tier_cards)
        lines += [
            f"## {TIER_TITLES[tier]}",
            "",
            "| " + " | ".join(COLUMNS) + " |",
            "|" + "---|" * len(COLUMNS),
            *(_row(c) for c in tier_cards),
            "",
            f"Tier {tier} subtotal: **{fmt_tokens(subtotal)}** tokens/arm "
            f"(≈ {subtotal / tps / 3600:.1f} h unsupervised at {tps:g} tok/s).",
            "",
        ]

    total_impl = sum(c.est_gen_tokens_per_arm for c in ordered if c.status == "implemented")
    total_all = sum(c.est_gen_tokens_per_arm for c in ordered)
    lines += [
        "## Totals",
        "",
        "| set | cards | tokens/arm | h/arm unsupervised |",
        "|---|---|---|---|",
        f"| implemented | {n_impl} | {fmt_tokens(total_impl)} | {total_impl / tps / 3600:.1f} |",
        f"| whole registry | {len(ordered)} | {fmt_tokens(total_all)} | {total_all / tps / 3600:.1f} |",
        "",
        "## Card details",
        "",
    ]
    for c in ordered:
        lines += [
            f"### `{c.id}` — {c.title}",
            "",
            f"*{c.purpose}*",
            "",
            f"- **Family / sensitivity:** {c.family} / {c.sensitivity}",
            f"- **Dataset:** `{c.dataset.source}`"
            + (f" @ `{c.dataset.revision}`" if c.dataset.revision else " (revision not yet pinned)")
            + f", {c.dataset.items} items"
            + (
                f" (subsample n={c.dataset.subsample.n}, stratify={c.dataset.subsample.stratify})"
                if c.dataset.subsample
                else ""
            ),
            f"- **Endpoints:** primary {', '.join(f'`{e}`' for e in c.endpoints.primary)}"
            + (
                f"; secondary {', '.join(f'`{e}`' for e in c.endpoints.secondary)}"
                if c.endpoints.secondary
                else ""
            )
            + f"; paired statistic `{c.paired_stat}`",
        ]
        if c.sesoi:
            lines.append(
                "- **SESOI:** " + ", ".join(f"`{k}` {v}" for k, v in sorted(c.sesoi.items()))
            )
        if c.task_args:
            lines.append(
                "- **Task args:** "
                + ", ".join(f"`{k}={v}`" for k, v in sorted(c.task_args.items()))
            )
        if c.exceptions:
            lines.append("- **Declared exceptions:** " + "; ".join(c.exceptions))
        if c.notes:
            lines.append(f"- **Notes:** {c.notes}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if docs/evals.md is stale.")
    parser.add_argument("--out", type=Path, default=DOC_PATH)
    args = parser.parse_args(argv)

    cards = load_cards()
    problems = {c.id: admission_problems(c) for c in cards.values()}
    problems = {k: v for k, v in problems.items() if v}
    if problems:
        for cid, errs in problems.items():
            print(f"{cid}:", file=sys.stderr)
            for e in errs:
                print(f"  - {e}", file=sys.stderr)
        print("refusing to render: cards fail admission", file=sys.stderr)
        return 1

    text = render(cards)
    if args.check:
        current = args.out.read_bytes().replace(b"\r\n", b"\n") if args.out.exists() else b""
        if current != text.encode("utf-8"):
            print(
                f"{args.out.relative_to(REPO_ROOT)} is stale; run `python scripts/gen_evals_doc.py`",
                file=sys.stderr,
            )
            return 1
        print(f"{args.out.relative_to(REPO_ROOT)} is up to date ({len(cards)} cards)")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(text.encode("utf-8"))
    print(f"wrote {args.out.relative_to(REPO_ROOT)} ({len(cards)} cards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
