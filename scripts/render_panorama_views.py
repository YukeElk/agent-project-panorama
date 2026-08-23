"""Render validated Panorama View IR candidates into offline HTML.

V0.6 bundles remain standalone. A V0.8 bundle with an Explain Pack is mounted
as a native, same-page Panorama canvas mode so the control, system, evolution,
Evidence Inspector, Architecture Studio, explanations, and finite flow motion
remain part of one governed delivery surface.
"""

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
PANORAMA_TEMPLATE = ROOT / "templates" / "panorama.html"
GUIDED_HOST_TEMPLATE = ROOT / "templates" / "panorama-guided-host.html"
MARKER = "__PANORAMA_VIEW_BUNDLE__"
GUIDED_BUNDLE_MARKER = "__PANORAMA_GUIDED_BUNDLE__"
RENDERER = {"id": "panorama-multi-view-renderer", "version": "0.4.0"}


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
    child_views: list[dict[str, Any]] | None = None,
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
        if view.get("profile") == "module_logic":
            raise PanoramaViewIRError(
                "module_logic 是嵌套子画布，必须通过 child_views 提供。"
            )
        errors = validate_view_ir(view, model)
        if errors:
            raise PanoramaViewIRError(
                f"View {view.get('viewId', '?')} 无效：" + "; ".join(errors[:20])
            )
        if view["viewId"] in view_ids:
            raise PanoramaViewIRError(f"View IR 重复：{view['viewId']}")
        view_ids.add(view["viewId"])
    child_views = child_views or []
    parent_by_id = {view["viewId"]: view for view in views}
    child_ids: set[str] = set()
    child_keys: set[tuple[str, str]] = set()
    for child in child_views:
        if child.get("profile") != "module_logic":
            raise PanoramaViewIRError("child_views 只接受 module_logic View IR。")
        parent = parent_by_id.get(child.get("parentViewBinding", {}).get("viewId"))
        if parent is None:
            raise PanoramaViewIRError(
                f"Module Logic View 缺少同 Bundle 父 View：{child.get('viewId', '?')}"
            )
        errors = validate_view_ir(child, model, parent_view=parent)
        if errors:
            raise PanoramaViewIRError(
                f"Child View {child.get('viewId', '?')} 无效："
                + "; ".join(errors[:20])
            )
        if child["viewId"] in view_ids or child["viewId"] in child_ids:
            raise PanoramaViewIRError(f"View IR 重复：{child['viewId']}")
        child_ids.add(child["viewId"])
        scope = child["filters"]["architectureScopes"][0]
        key = (child["rootModuleId"], scope)
        if key in child_keys:
            raise PanoramaViewIRError(
                f"Module Logic Child 重复：{key[0]} / {key[1]}"
            )
        child_keys.add(key)
    guided = view_set or build_view_set(model, views)
    view_set_errors = validate_view_set(guided, model, views)
    if view_set_errors:
        raise PanoramaViewIRError("View Set 无效：" + "; ".join(view_set_errors[:20]))
    if explain_pack is not None:
        if panorama is None:
            raise PanoramaViewIRError("接入 Explain Pack 时必须提供 Panorama Core。")
        explain_errors = validate_explain_pack(
            explain_pack,
            panorama,
            model,
            views,
            guided,
            child_views=child_views,
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
        "childViews": [
            {
                "viewId": view["viewId"],
                "semanticHash": view["integrity"]["semanticHash"],
                "layoutHash": view["integrity"]["layoutHash"],
                "rootModuleId": view["rootModuleId"],
                "parentViewId": view["parentViewBinding"]["viewId"],
            }
            for view in sorted(child_views, key=lambda item: item["viewId"])
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
        "childViews": sorted(child_views, key=lambda item: item["viewId"]),
        "explainPack": explain_pack,
    }


def _render_standalone_html(bundle: dict[str, Any]) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(MARKER) != 1:
        raise PanoramaViewIRError("Multi-view Template Marker 数量必须精确为 1。")
    payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
    return template.replace(MARKER, payload)


def _embed_panorama_data(template: str, panorama: dict[str, Any]) -> str:
    script_open = '<script id="project-panorama-data" type="application/json">'
    if template.count(script_open) != 1:
        raise PanoramaViewIRError("正式 Panorama Template 数据锚点无效。")
    payload_start = template.index(script_open) + len(script_open)
    payload_end = template.index("</script>", payload_start)
    payload = json.dumps(panorama, ensure_ascii=False, indent=2)
    payload = payload.replace("</script", "<\\/script")
    return template[:payload_start] + "\n" + payload + "\n  " + template[payload_end:]


def _render_integrated_html(
    bundle: dict[str, Any], panorama: dict[str, Any]
) -> str:
    panorama_template = PANORAMA_TEMPLATE.read_text(encoding="utf-8")
    host_template = GUIDED_HOST_TEMPLATE.read_text(encoding="utf-8")
    if panorama_template.count("</body>") != 1:
        raise PanoramaViewIRError("正式 Panorama Template 必须精确包含一个 </body>。")
    if host_template.count(GUIDED_BUNDLE_MARKER) != 1:
        raise PanoramaViewIRError(
            f"Guided Host Template Marker 数量无效：{GUIDED_BUNDLE_MARKER}"
        )
    payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
    host = host_template.replace(GUIDED_BUNDLE_MARKER, payload)
    integrated = _embed_panorama_data(panorama_template, panorama)
    return integrated.replace("</body>", host + "\n</body>", 1)


def render_html(
    bundle: dict[str, Any], *, panorama: dict[str, Any] | None = None
) -> str:
    if bundle.get("explainPack") is None:
        return _render_standalone_html(bundle)
    if panorama is None:
        raise PanoramaViewIRError(
            "V0.8 Explain Pack Renderer 必须嵌入正式 Panorama，不能生成替代主页面。"
        )
    return _render_integrated_html(bundle, panorama)


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="将同一 Model 的多个已验证 View IR 渲染为离线 HTML Candidate。"
    )
    parser.add_argument("model", type=Path, help="Panorama Model IR JSON")
    parser.add_argument(
        "--view", type=Path, action="append", required=True, dest="views"
    )
    parser.add_argument(
        "--child-view",
        type=Path,
        action="append",
        dest="child_views",
        help="可重复指定、绑定同 Bundle 父 View 的 module_logic 子画布",
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
        child_views = [_load_object(path) for path in (args.child_views or [])]
        view_set = _load_object(args.view_set) if args.view_set is not None else None
        if (args.panorama is None) != (args.explain_pack is None):
            raise PanoramaViewIRError("--panorama 与 --explain-pack 必须同时提供。")
        panorama = load_panorama(args.panorama)[0] if args.panorama is not None else None
        explain_pack = _load_object(args.explain_pack) if args.explain_pack is not None else None
        bundle = build_bundle(
            model,
            views,
            child_views=child_views,
            view_set=view_set,
            explain_pack=explain_pack,
            panorama=panorama,
        )
        atomic_write(args.output, render_html(bundle, panorama=panorama))
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
                "childViewCount": len(bundle["childViews"]),
                "explainPackId": (
                    bundle["explainPack"]["explainPackId"]
                    if bundle["explainPack"] is not None
                    else None
                ),
                "surface": (
                    "panorama-integrated-guided"
                    if bundle["explainPack"] is not None
                    else "standalone-multi-view"
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
