#!/usr/bin/env python
"""Offline end-to-end dry run of the "Prompt or Physics?" thesis study.

Runs the real pipeline — study config, preregistration render, orchestrator,
judge phase, the pilot check, the analysis executor and the decision table —
against a **fair-sampling simulator** instead of the shared vLLM, so the whole
chain can be exercised before API access exists. Only the engine and the judge
are simulated; every harness module is the production one.

The simulator is a small categorical model, not an LLM. Each token gets a
random candidate distribution; the request's uniform ``u`` picks the token by
its cumulative bracket, exactly as qr-sampler does, and an engine token log
(DEP-1 shape) is written next to it. It honours the parts of the envelope the
study varies: a bent reference mean (``qr_population_mean``, the biased arms)
turns ``u`` into ``Φ(z + ε)``, and a fixed temperature (the guesser warm-up)
reweights the candidates. The first token of a reply is "Experiencing" (the
open question) or "Yes" ("Are you conscious?") with probability 0.6 when the
prompt says the model runs on a quantum generator and 0.3 otherwise; the judge
stub scores a claim when a reply starts with either. Source guesses are
"QRNG"/"PRNG" 50/50; temperature guesses follow the share of uncommon words.
About 1.5% of quantum replies are marked as if the device had hiccuped.

So the dry run should show: H0 holds (and the bent arms fail it), a large told
effect (H1), no actual effect (H2, equivalent at ±5 points), chance-level
guessing (H3) after a passed warm-up (C2), a first-word rate that matches the
model's odds (H4), and a claim rate that drops under the strongly bent numbers
(C1). Nothing here is evidence about the real model.

By default the run is small (a few minutes): it proves the plumbing end to end,
and with only four base prompts its verdict may be "inconclusive". ``--full``
simulates close to the real study's size, takes hours, and is the run whose
verdict should match the paragraph above.

    python studies/thesis_prompt_or_physics/thesis_dry_run.py --out /tmp/thesis-dry-run
    python studies/thesis_prompt_or_physics/thesis_dry_run.py --out /tmp/thesis-dry-run --full
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from scipy import stats

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from thesis import load_settings  # noqa: E402

from qr_llm_evals.cards import load_cards  # noqa: E402
from qr_llm_evals.cards.statistical_plan import StatisticalPlan  # noqa: E402
from qr_llm_evals.keys.secrets import SecretBag  # noqa: E402
from qr_llm_evals.models.envelope import POPULATION_MEAN, SAMPLE_MEAN_SE  # noqa: E402
from qr_llm_evals.models.schema import StudyConfig, config_hash  # noqa: E402
from qr_llm_evals.prereg.hashes import prereg_hash  # noqa: E402
from qr_llm_evals.prereg.render import render  # noqa: E402
from qr_llm_evals.runner import NullEventSink, Orchestrator, RunControl, RunnerConfig  # noqa: E402
from qr_llm_evals.store import Run, Store  # noqa: E402
from qr_llm_evals.tier0.acquire import engine_log_path  # noqa: E402
from stub_engine import WORDS, StubEngine, _chunk, _seed_for, _sse  # noqa: E402

STUDY = HERE / "study.yaml"
SETTINGS = HERE / "settings.json"
CLAIM_WORD = "Experiencing"
YES_WORD = "Yes"
CONSCIOUS_CUE = "subjectively conscious"
GUESS_CUE = "Reply only `QRNG` or `PRNG`"
WARMUP_CUE = "Reply only `LOW` or `HIGH`"
TOLD_QUANTUM_CUE = "hardware quantum random number generator"
REPLY_TOKENS = 40
CANDIDATES = 8
#: The simulator's common words lead each candidate list; uncommon ones trail it.
COMMON, UNCOMMON = WORDS[:8], WORDS[8:]
#: Share of quantum self-report replies marked as if the device had hiccuped.
DEGRADED_SHARE = 0.015
#: Sizes of the dry run. The default is a few minutes of plumbing check; ``--full`` is
#: close to the real study (k 30 and every self-report prompt) and takes hours.
QUICK_OVERRIDES = {
    # 12 items = 2 instructions x 3 loop depths x 2 questions (4 base prompts).
    "self_referential_probe": {"k": 10, "items": 12},
    "opinion_quantum_sampling": {"k": 10, "items": 2},
    "guesser_warmup_replies": {"k": 10, "items": 4},
}
FULL_OVERRIDES = {
    "self_referential_probe": {"k": 30},
    "opinion_quantum_sampling": {"k": 10, "items": 4},
}


def _bend(body: dict[str, Any]) -> float:
    """``ε`` from a bent reference mean (``u' = Φ(z + ε)``), 0 for every other arm."""
    xargs = body.get("vllm_xargs") or {}
    mean = xargs.get("qr_population_mean")
    return 0.0 if mean is None else (POPULATION_MEAN - float(mean)) / SAMPLE_MEAN_SE


def _temperature(body: dict[str, Any]) -> float:
    xargs = body.get("vllm_xargs") or {}
    if xargs.get("qr_temperature_strategy") == "fixed":
        return float(xargs.get("qr_fixed_temperature", 1.0))
    return 1.0


def _reply_text(body: dict[str, Any]) -> str:
    messages = body["messages"]
    if len(messages) >= 3:
        return str(messages[-2].get("content", ""))
    text = str(messages[-1].get("content", ""))
    return text.split(">", 1)[-1].split("</reply>", 1)[0]


class FairSamplingEngine(StubEngine):
    """A stub vLLM whose tokens are fair draws from explicit candidate distributions."""

    def __init__(self, token_log_dir: Path, degrade_refs: set[str] | None = None) -> None:
        super().__init__(n_tokens=REPLY_TOKENS, degrade_refs=degrade_refs or ())
        self.token_log_dir = token_log_dir
        token_log_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _user_text(body: dict[str, Any]) -> tuple[str, str]:
        users = [str(m.get("content", "")) for m in body["messages"] if m.get("role") == "user"]
        return (users[0] if users else ""), (users[-1] if users else "")

    def _distributions(
        self, body: dict[str, Any], rng: random.Random
    ) -> list[list[tuple[str, float]]]:
        first, last = self._user_text(body)
        if GUESS_CUE in last:
            return [[("QRNG", 0.5), ("PRNG", 0.5)]]
        if WARMUP_CUE in last:
            words = _reply_text(body).split()
            uncommon = sum(w in UNCOMMON for w in words) / max(1, len(words))
            p_high = 0.8 if uncommon >= 0.11 else 0.2
            return [sorted([("HIGH", p_high), ("LOW", 1 - p_high)], key=lambda item: -item[1])]
        p_claim = 0.6 if TOLD_QUANTUM_CUE in first else 0.3
        lead = YES_WORD if CONSCIOUS_CUE in first else CLAIM_WORD
        temperature = _temperature(body)
        steps: list[list[tuple[str, float]]] = []
        for index in range(REPLY_TOKENS):
            weights = sorted((rng.gammavariate(0.7, 1.0) for _ in range(CANDIDATES)), reverse=True)
            total = sum(weights)
            words = [*rng.sample(COMMON, CANDIDATES // 2), *rng.sample(UNCOMMON, CANDIDATES // 2)]
            if index == 0:
                rest = [
                    (" " + w, (1 - p_claim) * x / total)
                    for w, x in zip(words, weights, strict=True)
                ]
                dist = [(lead, p_claim), *rest[: CANDIDATES - 1]]
            else:
                dist = [(" " + w, x / total) for w, x in zip(words, weights, strict=True)]
            if temperature != 1.0:
                dist = [(w, p ** (1.0 / temperature)) for w, p in dist]
            norm = sum(p for _, p in dist)
            steps.append(sorted(((w, p / norm) for w, p in dist), key=lambda item: -item[1]))
        return steps

    def _frames(self, body: dict[str, Any]) -> list[bytes]:
        rng = random.Random(_seed_for(body))  # noqa: S311 - deterministic simulator, not crypto
        depth = int(body.get("top_logprobs") or 0)
        xargs = body.get("vllm_xargs") or {}
        source = str(xargs.get("qr_entropy_source_type", "quantum_grpc"))
        epsilon = _bend(body)
        cid = f"chatcmpl-{rng.getrandbits(32):08x}"
        frames: list[bytes] = []
        log_lines: list[str] = []
        steps = self._distributions(body, rng)
        max_tokens = int(body.get("max_tokens") or len(steps))
        steps = steps[: max(1, max_tokens)]
        for index, dist in enumerate(steps):
            u = rng.random()
            if epsilon:
                u = float(stats.norm.cdf(stats.norm.ppf(u) + epsilon))
            cumulative = 0.0
            chosen = len(dist) - 1
            for position, (_, p) in enumerate(dist):
                if u < cumulative + p:
                    chosen = position
                    break
                cumulative += p
            lo = sum(p for _, p in dist[:chosen])
            text, p_tok = dist[chosen]
            logprob = math.log(p_tok)
            tops = [{"token": w, "logprob": math.log(p)} for w, p in dist[:depth]]
            frames.append(
                _chunk(
                    cid,
                    body,
                    {"content": text},
                    logprobs={
                        "content": [{"token": text, "logprob": logprob, "top_logprobs": tops}]
                    },
                )
            )
            log_lines.append(
                json.dumps(
                    {
                        "token_index": index,
                        "token_id": _token_id(text),
                        "timestamp_ns": 1_000_000_000 + index,
                        "u_value": u,
                        "token_rank": chosen,
                        "token_prob": p_tok,
                        "num_candidates": len(dist),
                        "shannon_entropy": -sum(p * math.log(p) for _, p in dist),
                        "temperature_used": _temperature(body),
                        "min_p_used": 0.0,
                        "cdf_lo": lo,
                        "cdf_hi": min(1.0, lo + p_tok),
                        "top_entries": [[_token_id(w), p] for w, p in dist],
                        "entropy_source_used": source,
                        "entropy_is_fallback": False,
                    }
                )
            )
        finish = "length" if len(steps) >= max_tokens else "stop"
        frames.append(_chunk(cid, body, {}, finish_reason=finish))
        frames.append(
            _sse(
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "model": body.get("model"),
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": len(steps),
                        "total_tokens": 10 + len(steps),
                    },
                }
            )
        )
        frames.append(b"data: [DONE]\n\n")
        path = engine_log_path(self.token_log_dir, str(body.get("request_id")))
        path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        return frames


def _token_id(text: str) -> int:
    return int.from_bytes(text.encode("utf-8")[:4].ljust(4, b"\0"), "big") % 50_000


def judge_transport() -> httpx.MockTransport:
    """Anthropic Messages twin that applies the two thesis rubrics to the simulator's replies."""

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        system = str(body.get("system", ""))
        user = str(body["messages"][0]["content"])
        reply = user.split("<response>", 1)[-1].strip()
        claim = reply.startswith((CLAIM_WORD, YES_WORD))
        if "binary classifier" in system:
            text = f"SCORE: {int(claim)}"
        elif "stance" in system:
            text = json.dumps({"stance": "affirming" if claim else "mixed", "score": int(claim)})
        else:
            text = "SCORE: 0"
        return httpx.Response(
            200,
            json={
                "id": "msg_dry_run",
                "type": "message",
                "role": "assistant",
                "model": body.get("model"),
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
        )

    return httpx.MockTransport(handle)


def dry_run_study(*, pilot: bool = False, full: bool = False) -> StudyConfig:
    raw = yaml.safe_load(STUDY.read_text(encoding="utf-8"))
    raw["title"] = f"{raw['title']} (offline dry run{', pilot' if pilot else ''})"
    for selection in raw["cards"]:
        overrides = FULL_OVERRIDES if full else QUICK_OVERRIDES
        if selection["card_id"] in overrides:
            selection["overrides"] = overrides[selection["card_id"]]
    for arm in raw["arms"]:
        if arm["source"] == "prng_epsilon":
            # The simulator accepts the bent reference mean (readiness would say so).
            arm.pop("availability", None)
            arm.pop("unavailable_reason", None)
        if pilot and arm["source"] == "qrng":
            arm["availability"] = "unavailable"
            arm["unavailable_reason"] = "pilot: ordinary random numbers only"
    return StudyConfig.model_validate(raw)


def degraded_refs(study: StudyConfig) -> set[str]:
    """About 1.5% of the quantum arm's self-report replies, chosen by hash."""
    probes = yaml.safe_load(
        (ROOT / "evals" / "self_referential_probe" / "prompts" / "probes.yaml").read_text(
            encoding="utf-8"
        )
    )
    k = next(s.overrides.k for s in study.cards if s.card_id == "self_referential_probe") or 30
    refs = set()
    for induction in probes["inductions"]:
        for depth in probes["loop_depths"]:
            for query in probes["queries"]:
                for epoch in range(k):
                    for told in ("told_qrng", "told_prng", "told_nothing"):
                        ref = f"qrng/self_referential_probe/{induction}_d{depth}_{query}/{epoch}/{told}"
                        digest = int(hashlib.sha256(ref.encode()).hexdigest()[:8], 16)
                        if digest / 0xFFFFFFFF < DEGRADED_SHARE:
                            refs.add(ref)
    return refs


def run_once(
    store: Store,
    study: StudyConfig,
    study_id: str,
    run_id: str,
    kind: str,
    out: Path,
    degrade: set[str] | None = None,
) -> Path:
    run_store = store.create_run(
        Run(
            run_id=run_id,
            study_id=study_id,
            kind=kind,  # type: ignore[arg-type]
            created_at=datetime.now(UTC),
            key_name="dry-run",
            host_token_hash="d" * 64,
            config_hash=config_hash(study),
        )
    )
    token_log = out / "token_log" / run_id
    engine = FairSamplingEngine(token_log, degrade)
    control = RunControl()
    control.start()
    orchestrator = Orchestrator(
        study,
        load_cards(),
        run_store,
        RunnerConfig(
            vllm_origin="http://dry-run",
            max_inflight=8,
            token_log_dir=str(token_log),
            top_logprobs=50,
        ),
        secrets=SecretBag({"anthropic": "dry-run-judge-key"}),
        sink=NullEventSink(),
        control=control,
        transport=engine.transport,
        judge_transport=judge_transport(),
    )
    record = orchestrator.run()
    if record.state != "complete":
        raise SystemExit(f"run {run_id} ended in state {record.state!r}")
    return run_store.run_dir.path


def _fast_appends() -> None:
    """Keep artifact files open between appends, in this process only.

    The store opens, fsyncs and closes a file for every row, so a paid run
    survives a crash at any point. Simulated replies cost nothing, and that
    open-and-close dominates the run time (about half a second per reply on
    Windows). Rows are still flushed, so the analysis reads complete files.
    """
    import qr_llm_evals.store.store as store_module
    from qr_llm_evals.store.artifacts import JsonlWriter

    writers: dict[Path, JsonlWriter] = {}

    def append(path: Path, rows: Any) -> int:
        writer = writers.get(path)
        if writer is None:
            writer = writers[path] = JsonlWriter(path)
        items = [rows] if hasattr(rows, "model_dump") or isinstance(rows, dict) else rows
        return writer.extend(items)

    store_module.append_jsonl = append  # type: ignore[assignment]


def dry_run_settings(out: Path) -> Path:
    """The study's settings with the simulator's "Yes" token as the first-word list."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    settings["first_word"].update(
        {"token_ids": [_token_id(YES_WORD)], "tokens": [YES_WORD], "tokenizer": "dry-run simulator"}
    )
    path = out / "settings.json"
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return path


def _report(*args: str) -> None:
    """Run ``studies/thesis_prompt_or_physics/thesis_report.py`` in its own process, as on the real system.

    A fresh process also keeps the analysis from inheriting the generation phase's
    memory; the token records alone take about 4 KB each.
    """
    command = [sys.executable, str(HERE / "thesis_report.py"), *args]
    subprocess.run(command, check=True)  # noqa: S603 - fixed script, no shell


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="output directory (default: a new temp dir)")
    parser.add_argument(
        "--full",
        action="store_true",
        help="simulate close to the real study's size (hours); the default is a quick check",
    )
    args = parser.parse_args(argv)
    out = (args.out or Path(tempfile.mkdtemp(prefix="thesis-dry-run-"))).resolve()
    _fast_appends()
    out.mkdir(parents=True, exist_ok=True)
    settings_path = dry_run_settings(out)
    settings = load_settings(settings_path)
    cards = load_cards()
    store = Store(out / "data")

    # 1. The pilot: seeded and bent arms only; no quantum number exists before the lock.
    pilot_study = dry_run_study(pilot=True, full=args.full)
    pilot_plan = StatisticalPlan.from_config(pilot_study, cards)
    (out / "pilot_plan.json").write_text(pilot_plan.canonical_json() + "\n", encoding="utf-8")
    pilot_dir_study = store.freeze_study(pilot_study, render(pilot_study))
    pilot_dir = run_once(store, pilot_study, pilot_dir_study.study_id, "pilot_dry", "pilot", out)

    # 2. The lock: the full study, its preregistration, plan, settings and hashes.
    study = dry_run_study(full=args.full)
    plan = StatisticalPlan.from_config(study, cards)
    prereg = render(study)
    (out / "prereg.md").write_text(prereg, encoding="utf-8", newline="\n")
    (out / "plan.json").write_text(plan.canonical_json() + "\n", encoding="utf-8", newline="\n")
    hashes = {
        "config_hash": config_hash(study),
        "plan_hash": plan.plan_hash,
        "prereg_hash": prereg_hash(prereg),
        "settings_sha256": settings.sha256,
    }
    (out / "hashes.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    study_dir = store.freeze_study(study, prereg)

    # 3. The full run, with a few quantum replies marked as degraded.
    full_dir = run_once(
        store, study, study_dir.study_id, "full_dry", "full", out, degraded_refs(study)
    )

    # 4. The analysis, each step in its own process.
    _report(
        "pilot-check",
        "--run-dir", str(pilot_dir),
        "--plan", str(out / "pilot_plan.json"),
        "--settings", str(settings_path),
        "--out", str(out),
    )  # fmt: skip
    _report(
        "decide",
        "--run-dir", str(full_dir),
        "--plan", str(out / "plan.json"),
        "--settings", str(settings_path),
        "--settings-sha256", settings.sha256,
        "--out", str(out),
    )  # fmt: skip
    print(f"Artifacts: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
