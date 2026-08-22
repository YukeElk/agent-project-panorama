"""Render validated Panorama View IR candidates into standalone HTML."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write, compute_canonical_hash
from panorama_explain_pack import validate_explain_pack
from panorama_view_ir import PanoramaViewIRError, validate_model_ir, validate_view_ir
from panorama_view_set import build_view_set, validate_view_set
from validate_panorama import ValidationRuntimeError, load_panorama


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "panorama-multi-view.html"
MARKER = "__PANORAMA_VIEW_BUNDLE__"
RENDERER = {"id": "panorama-multi-view-renderer", "version": "0.3.0"}


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PanoramaViewIRError(f"JSON 输入无效 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise PanoramaViewIRError(f"JSON 输入必须是 object：{path}")
    return value


def build_bundle(
    model: dict[str, Any],
    views: list[dict[str, Any]],
    *,
    view_set: dict[str, Any] | None = None,
    explain_pack: dict[str, Any] | None = None,
    panorama: dict[str, Any] | None = None,
) -> dict[str, Any]:
    model_errors = validate_model_ir(model)
    if model_errors:
        raise PanoramaViewIRError("Model IR 无效：" + "; ".join(model_errors[:20]))
    if not views:
        raise PanoramaViewIRError("至少需要一个 View IR。")
    view_ids: set[str] = set()
    for view in views:
        errors = validate_view_ir(view, model)
        if errors:
            raise PanoramaViewIRError(
                f"View {view.get('viewId', '?')} 无效：" + "; ".join(errors[:20])
            )
        if view["viewId"] in view_ids:
            raise PanoramaViewIRError(f"View IR 重复：{view['viewId']}")
        view_ids.add(view["viewId"])
    guided = view_set or build_view_set(model, views)
    view_set_errors = validate_view_set(guided, model, views)
    if view_set_errors:
        raise PanoramaViewIRError("View Set 无效：" + "; ".join(view_set_errors[:20]))
    if explain_pack is not None:
        if panorama is None:
            raise PanoramaViewIRError("接入 Explain Pack 时必须提供 Panorama Core。")
        explain_errors = validate_explain_pack(
            explain_pack, panorama, model, views, guided
        )
        if explain_errors:
            raise PanoramaViewIRError(
                "Explain Pack 无效：" + "; ".join(explain_errors[:20])
            )
    view_by_id = {view["viewId"]: view for view in views}
    ordered = [view_by_id[chapter["viewId"]] for chapter in guided["chapters"]]
    semantics = {
        "renderer": RENDERER,
        "modelId": model["modelId"],
        "modelSemanticHash": model["integrity"]["semanticHash"],
        "viewSetId": guided["viewSetId"],
        "viewSetSemanticHash": guided["integrity"]["semanticHash"],
        "views": [
            {
                "viewId": view["viewId"],
                "semanticHash": view["integrity"]["semanticHash"],
                "layoutHash": view["integrity"]["layoutHash"],
            }
            for view in ordered
        ],
        "explainPack": (
            {
                "explainPackId": explain_pack["explainPackId"],
                "semanticHash": explain_pack["integrity"]["semanticHash"],
            }
            if explain_pack is not None
            else None
        ),
    }
    bundle_hash = compute_canonical_hash(semantics)
    return {
        "formatVersion": (
            "panorama-multi-view-bundle.v0.2"
            if explain_pack is not None
            else "panorama-multi-view-bundle.v0.1"
        ),
        "bundleId": f"PVB-{bundle_hash[:24].upper()}",
        "bundleHash": bundle_hash,
        "renderer": RENDERER,
        "model": model,
        "viewSet": guided,
        "views": ordered,
        "explainPack": explain_pack,
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
    parser.add_argument(
        "--view-set", type=Path, help="可选的已验证 Guided View Set JSON"
    )
    parser.add_argument("--panorama", type=Path, help="Explain Pack 绑定的 Panorama Core")
    parser.add_argument("--explain-pack", type=Path, help="可选的已验证 Explain Pack JSON")
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
        view_set = _load_object(args.view_set) if args.view_set is not None else None
        if (args.panorama is None) != (args.explain_pack is None):
            raise PanoramaViewIRError("--panorama 与 --explain-pack 必须同时提供。")
        panorama = load_panorama(args.panorama)[0] if args.panorama is not None else None
        explain_pack = _load_object(args.explain_pack) if args.explain_pack is not None else None
        bundle = build_bundle(
            model,
            views,
            view_set=view_set,
            explain_pack=explain_pack,
            panorama=panorama,
        )
        atomic_write(args.output, render_html(bundle))
    except (
        OSError,
        UnicodeError,
        ValidationRuntimeError,
        PanoramaViewIRError,
    ) as exc:
        print(f"Renderer 生成失败：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "rendered",
                "output": str(args.output),
                "bundleId": bundle["bundleId"],
                "bundleHash": bundle["bundleHash"],
                "viewSetId": bundle["viewSet"]["viewSetId"],
                "viewCount": len(bundle["views"]),
                "explainPackId": (
                    bundle["explainPack"]["explainPackId"]
                    if bundle["explainPack"] is not None
                    else None
                ),
                "visualReview": "pending",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
