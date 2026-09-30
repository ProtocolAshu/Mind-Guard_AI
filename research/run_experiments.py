"""Baselines vs proposed vs ablations on the digital-user simulator (sections 27, 55).

    PYTHONPATH=backend:. python -m research.run_experiments --seeds 10 --days 21 --warmup 7

Every controller faces identical simulated days (common random numbers), so comparisons
are paired by (persona, seed). Results are synthetic and must not be reported as user-study
evidence.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from app.risk.scoring import RiskModelRegistry, RiskScorer
from ml.common import MODEL_DIR
from research.stats import bootstrap_ci, holm, paired_comparison
from simulator.controllers import controller_factories
from simulator.personas import PERSONAS
from simulator.runner import run_episode

ROOT = Path(__file__).resolve().parent
ORDER = ["B1_no_intervention", "B2_static_limit", "B3_reminder_only", "B4_rule_based", "A_no_memory", "B_no_context",
         "C_no_personalization", "D_no_bandit", "E_rule_only", "F_full_system"]
METRICS = {  # name: (higher_is_better, description)
    "unwanted_minutes": (False, "Unwanted minutes/day (off-goal content during focus, or beyond 30 min)"),
    "social_minutes": (False, "Social-media minutes/day"),
    "long_sessions": (False, "Sessions >= 30 min per day"),
    "goal_adherence": (True, "Share of focus time not spent on off-goal social content"),
    "interventions": (False, "Interventions per day"),
    "acceptance_rate": (True, "Accepted / interventions"),
    "override_rate": (False, "Overridden / interventions"),
    "false_positive_rate": (False, "Interventions on goal-relevant content / interventions"),
    "satisfaction": (True, "Simulated satisfaction index per day (see simulator/world.py)"),
}


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    df = pd.DataFrame(rows)
    n = df["interventions"].sum()
    return {"unwanted_minutes": df["unwanted_minutes"].mean(), "social_minutes": df["social_minutes"].mean(),
            "long_sessions": df["long_sessions"].mean(), "goal_adherence": df["goal_adherence"].mean(),
            "interventions": df["interventions"].mean(), "acceptance_rate": df["accepted"].sum() / n if n else np.nan,
            "override_rate": df["overridden"].sum() / n if n else np.nan,
            "false_positive_rate": df["false_positives"].sum() / n if n else np.nan,
            "satisfaction": df["satisfaction"].mean()}


CHUNK_DIR = ROOT / "results" / "chunks"


def run_chunks(seeds: int, days: int, warmup: int, budget_seconds: float) -> int:
    """Run missing (persona, seed) chunks until the time budget is used; returns chunks still missing."""
    registry = RiskModelRegistry(MODEL_DIR)
    scorer = RiskScorer(registry if registry.available else None)
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    todo = [(p, s) for p in PERSONAS for s in range(seeds) if not (CHUNK_DIR / f"{p.name}__{s}.json").exists()]
    for persona, seed in todo:
        if time.perf_counter() - started > budget_seconds:
            break
        t0 = time.perf_counter()
        factories = controller_factories(persona, scorer)
        episodes, daily = [], []
        for name in ORDER:
            rows = run_episode(persona, factories[name](), seed=seed, days=days)  # type: ignore[operator]
            daily.extend({"persona": persona.name, "seed": seed, "controller": name, **r} for r in rows)
            episodes.append({"persona": persona.name, "seed": seed, "controller": name, **aggregate(rows[warmup:])})
        payload = {"persona": persona.name, "seed": seed, "days": days, "warmup": warmup, "risk_model": registry.version_string() or "rules-only",
                   "runtime_seconds": round(time.perf_counter() - t0, 2), "episodes": episodes, "daily": daily}
        tmp = CHUNK_DIR / f".{persona.name}__{seed}.json.tmp"
        tmp.write_text(json.dumps(payload, default=float))
        tmp.replace(CHUNK_DIR / f"{persona.name}__{seed}.json")
        print(f"  chunk {persona.name} seed={seed} {payload['runtime_seconds']}s", flush=True)
    return sum(1 for p in PERSONAS for s in range(seeds) if not (CHUNK_DIR / f"{p.name}__{s}.json").exists())


def load_chunks(seeds: int, days: int, warmup: int) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    episodes, daily, runtime, models = [], [], 0.0, set()
    for persona in PERSONAS:
        for seed in range(seeds):
            path = CHUNK_DIR / f"{persona.name}__{seed}.json"
            if not path.exists():
                raise SystemExit(f"missing chunk {path.name}: run without --analyse first")
            payload = json.loads(path.read_text())
            if payload["days"] != days or payload["warmup"] != warmup:
                raise SystemExit(f"chunk {path.name} was produced with different settings")
            episodes.extend(payload["episodes"])
            daily.extend(payload["daily"])
            runtime += payload["runtime_seconds"]
            models.add(payload["risk_model"])
    meta = {"seeds": seeds, "days": days, "warmup_days": warmup, "personas": [p.name for p in PERSONAS],
            "risk_model": ", ".join(sorted(models)), "runtime_seconds": round(runtime, 1)}
    return pd.DataFrame(episodes), pd.DataFrame(daily), meta


def analyse(ep: pd.DataFrame) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary: dict[str, Any] = {}
    for name in ORDER:
        part = ep[ep["controller"] == name]
        summary[name] = {}
        for metric in METRICS:
            mean, lo, hi = bootstrap_ci(part[metric].to_numpy())
            summary[name][metric] = {"mean": mean, "ci_lo": lo, "ci_hi": hi}
    pivot = {m: ep.pivot_table(index=["persona", "seed"], columns="controller", values=m, dropna=False).reindex(columns=ORDER)
             for m in METRICS}
    comparisons = []
    for metric in METRICS:
        for other in ORDER:
            if other == "F_full_system":
                continue
            result = paired_comparison(pivot[metric]["F_full_system"].to_numpy(), pivot[metric][other].to_numpy())
            comparisons.append({"metric": metric, "comparison": f"F_full_system - {other}", **result})
    adjusted = holm([c["p_value"] for c in comparisons])
    for c, p in zip(comparisons, adjusted, strict=True):
        c["p_holm"] = p
    return summary, comparisons


def figures(ep: pd.DataFrame, daily: pd.DataFrame, summary: dict[str, Any], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    colors = ["#9aa5b1"] * 4 + ["#7fb3d5"] * 5 + ["#1f6f8b"]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8.5))
    for ax, metric in zip(axes.flat, ["unwanted_minutes", "social_minutes", "override_rate", "satisfaction"], strict=True):
        means = [summary[c][metric]["mean"] for c in ORDER]
        err = np.array([[summary[c][metric]["mean"] - summary[c][metric]["ci_lo"] for c in ORDER],
                        [summary[c][metric]["ci_hi"] - summary[c][metric]["mean"] for c in ORDER]])
        ax.bar(range(len(ORDER)), np.nan_to_num(means), yerr=np.nan_to_num(err), color=colors, capsize=3)
        ax.set_xticks(range(len(ORDER)), ORDER, rotation=55, ha="right", fontsize=8)
        ax.set_title(METRICS[metric][1], fontsize=9)
    fig.suptitle("Simulated comparison (mean and 95% bootstrap CI over persona x seed episodes)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "controller_comparison.png", dpi=130)
    plt.close(fig)

    base = ep[ep["controller"] == "B1_no_intervention"].set_index(["persona", "seed"])["unwanted_minutes"]
    table = {}
    for name in ORDER[1:]:
        cur = ep[ep["controller"] == name].set_index(["persona", "seed"])["unwanted_minutes"]
        change = ((cur - base) / base.replace(0, np.nan) * 100).groupby(level=0).mean()
        table[name] = change
    heat = pd.DataFrame(table)
    fig, ax = plt.subplots(figsize=(11, 3.8))
    im = ax.imshow(heat.to_numpy(), cmap="RdYlGn_r", vmin=-100, vmax=100, aspect="auto")
    ax.set_xticks(range(heat.shape[1]), heat.columns, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(heat.shape[0]), heat.index, fontsize=8)
    for i in range(heat.shape[0]):
        for j in range(heat.shape[1]):
            ax.text(j, i, f"{heat.iat[i, j]:+.0f}%", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, label="% change in unwanted minutes vs no intervention")
    ax.set_title("Unwanted minutes relative to Baseline 1, by persona (evaluation days)")
    fig.tight_layout()
    fig.savefig(out / "persona_unwanted_heatmap.png", dpi=130)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    for metric, ax in zip(["overridden", "accepted"], axes, strict=True):
        for name in ("F_full_system", "D_no_bandit", "A_no_memory", "E_rule_only"):
            part = daily[daily["controller"] == name].groupby("day")[[metric, "interventions"]].sum()
            ax.plot(part.index, part[metric] / part["interventions"].replace(0, np.nan), label=name)
        ax.set(title=f"Daily {metric} rate across all personas", xlabel="simulated day", ylabel="rate")
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "learning_curves.png", dpi=130)
    plt.close(fig)

    mix_cols = [c for c in daily.columns if c.startswith("n_")]
    mix = daily[daily["controller"] == "F_full_system"].groupby("persona")[mix_cols].sum()
    mix = mix.div(mix.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    fig, ax = plt.subplots(figsize=(9, 3.8))
    left = np.zeros(len(mix))
    for col in mix.columns:
        ax.barh(mix.index, mix[col], left=left, label=col[2:])
        left += mix[col].to_numpy()
    ax.set(title="Intervention mix chosen by the full system, per persona", xlabel="share of interventions")
    ax.legend(fontsize=7, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    fig.tight_layout()
    fig.savefig(out / "intervention_mix.png", dpi=130)
    plt.close(fig)


def markdown(summary: dict[str, Any], comparisons: list[dict[str, Any]], ep: pd.DataFrame, meta: dict[str, Any]) -> str:
    lines = ["# Simulation results (synthetic)", "",
             f"Generated by `research/run_experiments.py`: {len(meta['personas'])} personas x {meta['seeds']} seeds x "
             f"{meta['days']} days per controller; the first {meta['warmup_days']} days are a learning warm-up and are excluded. "
             f"Risk model: `{meta['risk_model']}`. Runtime {meta['runtime_seconds']} s.", "",
             "> These are results of a synthetic simulator whose user model was written by the authors. They show how the",
             "> decision logic behaves under stated assumptions; they are **not** evidence of real-world efficacy.", "",
             "## Controller means (95% bootstrap CI)", "", "| Controller | " + " | ".join(METRICS) + " |", "|---|" + "---|" * len(METRICS)]
    for name in ORDER:
        cells = []
        for metric in METRICS:
            s = summary[name][metric]
            cells.append("n/a" if not np.isfinite(s["mean"]) else f"{s['mean']:.2f} [{s['ci_lo']:.2f}, {s['ci_hi']:.2f}]")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "## Paired comparisons: full system minus each controller", "",
              "Wilcoxon signed-rank over matched (persona, seed) episodes, Holm-corrected across all tests; r = rank-biserial.", "",
              "| Metric | vs | mean diff [95% CI] | p (Holm) | r |", "|---|---|---|---|---|"]
    for c in comparisons:
        if c["n"] == 0:
            continue
        if c["metric"] in ("unwanted_minutes", "override_rate", "satisfaction", "goal_adherence", "false_positive_rate"):
            other = c["comparison"].split(" - ")[1]
            lines.append(f"| {c['metric']} | {other} | {c['mean_diff']:+.3f} [{c['ci_lo']:+.3f}, {c['ci_hi']:+.3f}] | "
                         f"{c['p_holm']:.3g} | {c['rank_biserial']:+.2f} |")
    lines += ["", "## Per-persona unwanted minutes/day", "",
              ep.pivot_table(index="persona", columns="controller", values="unwanted_minutes").reindex(columns=ORDER).round(1).to_markdown(), "",
              "Figures: `research/figures/controller_comparison.png`, `persona_unwanted_heatmap.png`, `learning_curves.png`, "
              "`intervention_mix.png`.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--warmup", type=int, default=7)
    parser.add_argument("--budget-seconds", type=float, default=float("inf"), help="stop after this many seconds (resumable)")
    parser.add_argument("--analyse", action="store_true", help="aggregate completed chunks into results and figures")
    args = parser.parse_args()
    if not args.analyse:
        remaining = run_chunks(args.seeds, args.days, args.warmup, args.budget_seconds)
        print(f"remaining chunks: {remaining}")
        if remaining:
            return
    ep, daily, meta = load_chunks(args.seeds, args.days, args.warmup)
    summary, comparisons = analyse(ep)
    results = ROOT / "results"
    ep.to_csv(results / "episodes.csv", index=False)
    daily.to_csv(results / "daily.csv", index=False)
    pd.DataFrame(comparisons).to_csv(results / "comparisons.csv", index=False)
    (results / "summary.json").write_text(json.dumps({"meta": meta, "summary": summary, "comparisons": comparisons}, indent=2, default=float))
    figures(ep, daily, summary, ROOT / "figures")
    (results / "summary.md").write_text(markdown(summary, comparisons, ep, meta))
    print((results / "summary.md").read_text()[:7000])


if __name__ == "__main__":
    main()
