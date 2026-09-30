"""python -m simulator generate --out ml/datasets/synthetic --seeds 8 --days 21
python -m simulator episode --persona night_owl_student --controller F_full_system --days 7"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from simulator.controllers import controller_factories
from simulator.dataset import generate
from simulator.personas import PERSONAS
from simulator.runner import run_episode


def _episode(args: argparse.Namespace) -> None:
    """Run one persona through one controller for a handful of days and print a summary.

    This is the "quick check" `make simulate` promises: no database, no served-model files
    required (falls back to the interpretable rules if none are registered), fast enough to
    run on every change to the decision stack.
    """
    persona = next((p for p in PERSONAS if p.name == args.persona), None)
    if persona is None:
        names = ", ".join(p.name for p in PERSONAS)
        raise SystemExit(f"unknown persona {args.persona!r}; choose one of: {names}")

    scorer = None
    if not args.no_model:
        from app.risk.scoring import RiskModelRegistry, RiskScorer

        from ml.common import MODEL_DIR

        registry = RiskModelRegistry(MODEL_DIR)
        scorer = RiskScorer(registry if registry.available else None)

    factories = controller_factories(persona, scorer)
    if args.controller not in factories:
        names = ", ".join(sorted(factories))
        raise SystemExit(f"unknown controller {args.controller!r}; choose one of: {names}")

    rows = run_episode(persona, factories[args.controller](), seed=args.seed, days=args.days)  # type: ignore[operator]
    n = len(rows)
    interventions = sum(r["interventions"] for r in rows)
    summary = {
        "persona": persona.name,
        "controller": args.controller,
        "seed": args.seed,
        "days": n,
        "risk_model": "served" if scorer is not None and scorer.registry is not None and scorer.registry.available else "rules-only",
        "avg_social_minutes_per_day": round(sum(r["social_minutes"] for r in rows) / n, 1),
        "avg_unwanted_minutes_per_day": round(sum(r["unwanted_minutes"] for r in rows) / n, 1),
        "total_interventions": interventions,
        "acceptance_rate": round(sum(r["accepted"] for r in rows) / interventions, 3) if interventions else None,
        "override_rate": round(sum(r["overridden"] for r in rows) / interventions, 3) if interventions else None,
        "avg_satisfaction_per_day": round(sum(r["satisfaction"] for r in rows) / n, 3),
    }
    print(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="MindGuard synthetic data generator and simulator")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="Generate the synthetic training dataset")
    g.add_argument("--out", type=Path, default=Path("ml/datasets/synthetic"))
    g.add_argument("--seeds", type=int, default=8)
    g.add_argument("--days", type=int, default=21)
    g.add_argument("--captions-per-class", type=int, default=400)

    e = sub.add_parser("episode", help="Run one persona through one controller (quick check)")
    e.add_argument("--persona", default=PERSONAS[0].name, choices=[p.name for p in PERSONAS])
    e.add_argument("--controller", default="F_full_system")
    e.add_argument("--seed", type=int, default=0)
    e.add_argument("--days", type=int, default=7)
    e.add_argument("--no-model", action="store_true", help="Skip loading served risk models; use rules only")

    args = parser.parse_args()
    if args.command == "generate":
        counts = generate(args.out, seeds=range(args.seeds), days=args.days, captions_per_class=args.captions_per_class)
        print(json.dumps(counts, indent=2))
    else:
        _episode(args)


if __name__ == "__main__":
    main()
