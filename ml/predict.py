"""Score one situation with the served risk stack (rules + registered ML models).

    PYTHONPATH=backend:. python -m ml.predict '{"session_minutes": 42, "hour": 23, "content_category": "short_form_video",
                                               "content_confidence": 0.8, "app_category": "video", "opens_last_hour": 4}'
"""

from __future__ import annotations

import json
import sys

from app.risk.features import RiskInput
from app.risk.scoring import RiskModelRegistry, RiskScorer
from app.schemas.common import AppCategory, ContentCategory
from ml.common import MODEL_DIR


def main(argv: list[str]) -> None:
    raw = json.loads(argv[1] if len(argv) > 1 else "{}")
    if "content_category" in raw:
        raw["content_category"] = ContentCategory(raw["content_category"])
    if "app_category" in raw:
        raw["app_category"] = AppCategory(raw["app_category"])
    if "trigger_hours" in raw:
        raw["trigger_hours"] = tuple(raw["trigger_hours"])
    raw.setdefault("session_minutes", 0.0)
    raw.setdefault("hour", 12)
    registry = RiskModelRegistry(MODEL_DIR)
    scorer = RiskScorer(registry)
    print(json.dumps({"load_errors": registry.load_errors, "assessment": scorer.score(RiskInput(**raw)).model_dump(mode="json")}, indent=2))


if __name__ == "__main__":
    main(sys.argv)
