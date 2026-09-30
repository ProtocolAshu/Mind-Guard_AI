"""Generates reference docs from the code so they cannot drift:
docs/api/openapi.json, docs/api.md (endpoint reference) and docs/configuration.md (environment variables).

    PYTHONPATH=backend python scripts/generate_docs.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "docs-generation-secret-docs-generation-secret")

from pydantic import SecretStr  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

SECRET_FIELDS = {"jwt_secret", "llm_api_key", "vision_api_key", "embedding_api_key", "metrics_token"}


def api_reference(spec: dict[str, Any]) -> str:
    by_tag: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            tag = (op.get("tags") or ["Other"])[0]
            by_tag.setdefault(tag, []).append((method.upper(), path, op))
    lines = [
        "# API reference",
        "",
        "Generated from the FastAPI OpenAPI schema by `scripts/generate_docs.py`; do not edit by hand.",
        f"{sum(len(v) for v in by_tag.values())} operations on {len(spec['paths'])} paths. The machine-readable schema is",
        "[`docs/api/openapi.json`](api/openapi.json); a running backend also serves Swagger UI at `/docs`.",
        "",
        "Authentication: `Authorization: Bearer <access token>` from `POST /api/auth/login`. Access tokens last",
        "`ACCESS_TOKEN_TTL_MINUTES`; refresh tokens rotate on every use and reuse revokes the whole token family.",
        "Errors use one envelope: `{\"error\": {\"code\", \"message\", \"details\", \"request_id\"}}`.",
        "",
    ]
    for tag in sorted(by_tag):
        lines += [f"## {tag}", "", "| Method | Path | Summary |", "|---|---|---|"]
        for method, path, op in sorted(by_tag[tag], key=lambda x: (x[1], x[0])):
            summary = (op.get("summary") or "").replace("|", "\\|")
            lines.append(f"| `{method}` | `{path}` | {summary} |")
        lines.append("")
    return "\n".join(lines)


def configuration_reference() -> str:
    lines = [
        "# Configuration",
        "",
        "Generated from `backend/app/core/config.py` by `scripts/generate_docs.py`; do not edit by hand.",
        "Every setting is read from an environment variable of the same name in upper case (or from `.env`).",
        "Secrets have no usable default; in `production` the backend refuses to start with a weak `JWT_SECRET`.",
        "",
        "| Variable | Type | Default |",
        "|---|---|---|",
    ]
    for name, field in Settings.model_fields.items():
        annotation = str(field.annotation).replace("typing.", "").replace("<class '", "").replace("'>", "").replace("|", "\\|")
        default = field.default
        if name in SECRET_FIELDS or isinstance(default, SecretStr):
            shown = "*(secret, required when used)*"
        elif name in {"model_dir", "knowledge_dir"}:
            shown = f"`{Path(str(default)).relative_to(ROOT)}`"
        else:
            shown = f"`{default!r}`" if default not in (None, "") else "—"
        lines.append(f"| `{name.upper()}` | {annotation} | {shown} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    spec = create_app().openapi()
    (DOCS / "api").mkdir(parents=True, exist_ok=True)
    (DOCS / "api" / "openapi.json").write_text(json.dumps(spec, indent=2) + "\n")
    (DOCS / "api.md").write_text(api_reference(spec) + "\n")
    (DOCS / "configuration.md").write_text(configuration_reference())
    print(f"wrote docs/api.md ({len(spec['paths'])} paths), docs/api/openapi.json, docs/configuration.md")


if __name__ == "__main__":
    main()
