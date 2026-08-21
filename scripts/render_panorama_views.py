"""Render validated Panorama View IR candidates into standalone HTML."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write, compute_canonical_hash
from panorama_view_ir import PanoramaViewIRError, validate_model_ir, validate_view_ir


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "panorama-multi-view.html"
MARKER = "__PANORAMA_VIEW_BUNDLE__"
RENDERER = {"id": "panorama-multi-view-renderer", "version": "0.1.0"}
PROFILE_ORDER = {
    "module": 0,
    "dependency_dataflow": 1,
    "deployment_runtime": 2,
    "sequence": 3,
    "lifecycle": 4,
    "evolution_risk": 5,
}


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PanoramaViewIRError(f"JSON 输入无效 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise PanoramaViewIRError(f"JSON 输入必须是 object：{path}")
    return value


def build_bundle(model: dict[str, Any], views: list[dict[str, Any]]) -> dict[str, Any]:
    model_errors = validate_model_ir(model)
    if model_errors:
        raise PanoramaViewIRError("Model IR 无效：" + "; ".join(model_errors[:20]))
    if not views:
        raise PanoramaViewIRError("至少需要一个 View IR。")
    profiles: set[str] = set()
    for view in views:
        errors = validate_view_ir(view, model)
        if errors:
            raise PanoramaViewIRError(
                f"View {view.get('viewId', '?')} 无效：" + "; ".join(errors[:20])
            )
        if view["profile"] in profiles:
            raise PanoramaViewIRError(f"同一 profile 重复：{view['profile']}")
        profiles.add(view["profile"])
    ordered = sorted(
        views,
        key=lambda item: (PROFILE_ORDER.get(item["profile"], 99), item["viewType"]),
    )
    semantics = {
        "renderer": RENDERER,
        "modelId": model["modelId"],
        "modelSemanticHash": model["integrity"]["semanticHash"],
        "views": [
            {
                "viewId": view["viewId"],
                "semanticHash": view["integrity"]["semanticHash"],
                "layoutHash": view["integrity"]["layoutHash"],
            }
            for view in ordered
        ],
    }
    bundle_hash = compute_canonical_hash(semantics)
    return {
        "formatVersion": "panorama-multi-view-bundle.v0.1",
        "bundleId": f"PVB-{bundle_hash[:24].upper()}",
        "bundleHash": bundle_hash,
        "renderer": RENDERER,
        "model": model,
        "views": ordered,
    }


def render_html(bundle: dict[str, Any]) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(MARKER) != 1:
        raise PanoramaViewIRError("Multi-view Template Marker 数量必须精确为 1。")
    payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
    return template.replace(MARKER, payload)


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="将同一 Model 的多个已验证 View IR 渲染为离线 HTML Candidate。"
    )
    parser.add_argument("model", type=Path, help="Panorama Model IR JSON")
    parser.add_argument(
        "--view", type=Path, action="append", required=True, dest="views"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.output.exists() and not args.overwrite:
        print(f"Renderer Candidate 已存在：{args.output}", file=sys.stderr)
        return 2
    try:
        model = _load_object(args.model)
        views = [_load_object(path) for path in args.views]
        bundle = build_bundle(model, views)
        atomic_write(args.output, render_html(bundle))
    except (OSError, UnicodeError, PanoramaViewIRError) as exc:
        print(f"Renderer 生成失败：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "rendered",
                "output": str(args.output),
                "bundleId": bundle["bundleId"],
                "bundleHash": bundle["bundleHash"],
                "viewCount": len(bundle["views"]),
                "visualReview": "pending",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
