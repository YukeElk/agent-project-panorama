"""Render a validated Explain Pack as a Codex inline Fragment plus Markdown fallback."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from panorama_cli import ChineseArgumentParser
from panorama_explain_pack import PanoramaExplainPackError, validate_explain_pack
from panorama_io import atomic_write
from panorama_view_ir import PanoramaViewIRError
from validate_panorama import ValidationRuntimeError, load_panorama


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "panorama-explainer-fragment.html"
ROOT_MARKER = "__PANORAMA_EXPLAIN_ROOT__"
PAYLOAD_MARKER = "__PANORAMA_EXPLAIN_PAYLOAD__"
MAX_FRAGMENT_BYTES = 1024 * 1024


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PanoramaExplainPackError(f"JSON 输入无效 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise PanoramaExplainPackError(f"JSON 输入必须是 object：{path}")
    return value


def _visual(view: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": node["id"],
                "label": node["label"],
                "entityId": node["entityRef"]["id"],
            }
            for node in view["nodes"][:64]
        ],
        "edges": [
            {
                "id": edge["id"],
                "label": edge["label"],
                "from": edge["fromNodeId"],
                "to": edge["toNodeId"],
            }
            for edge in view["edges"][:96]
        ],
    }


def build_fragment_payload(
    pack: dict[str, Any], views: list[dict[str, Any]]
) -> dict[str, Any]:
    view_by_id = {view["viewId"]: view for view in views}
    stories = []
    for story in pack["stories"]:
        view = view_by_id[story["viewBinding"]["viewId"]]
        stories.append({**story, "visual": _visual(view)})
    return {
        "formatVersion": "panorama-codex-explainer-fragment.v0.1",
        "explainPackId": pack["explainPackId"],
        "explainPackSemanticHash": pack["integrity"]["semanticHash"],
        "projectId": pack["projectBinding"]["projectId"],
        "stories": stories,
    }


def render_fragment(pack: dict[str, Any], views: list[dict[str, Any]]) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(PAYLOAD_MARKER) != 1 or ROOT_MARKER not in template:
        raise PanoramaExplainPackError("Codex Fragment Template Marker 无效。")
    root_id = "panorama-explain-" + pack["explainPackId"].lower()
    payload = json.dumps(build_fragment_payload(pack, views), ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
    fragment = template.replace(ROOT_MARKER, root_id).replace(PAYLOAD_MARKER, payload)
    lowered = fragment.lower()
    forbidden = ("<!doctype", "<html", "<head", "<body", "fetch(", "xmlhttprequest", "websocket")
    if any(token in lowered for token in forbidden):
        raise PanoramaExplainPackError("Codex Fragment 含不允许的 Document 或 Network 能力。")
    if fragment.count(f'id="{root_id}"') != 1:
        raise PanoramaExplainPackError("Codex Fragment 根节点必须精确唯一。")
    if len(fragment.encode("utf-8")) > MAX_FRAGMENT_BYTES:
        raise PanoramaExplainPackError(f"Codex Fragment 超过 {MAX_FRAGMENT_BYTES} bytes。")
    return fragment


def render_markdown(pack: dict[str, Any]) -> str:
    lines = [
        f"# 项目全景逐步讲解 · {pack['projectBinding']['projectId']}",
        "",
        f"Explain Pack: `{pack['explainPackId']}`",
        "",
    ]
    for story in pack["stories"]:
        lines.extend([f"## {story['title']}", "", story["summary"], ""])
        for step in story["steps"]:
            lines.extend(
                [
                    f"### {step['order'] + 1}. {step['title']}",
                    "",
                    step["summary"],
                    "",
                ]
            )
            lines.extend(f"- {claim['text']}" for claim in step["claimBlocks"])
            if step["caveats"]:
                lines.append(f"- 边界：{'；'.join(step['caveats'])}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _inside_repository(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="将 Explain Pack 渲染为 Codex 逐步交互 Fragment。")
    parser.add_argument("panorama", type=Path)
    parser.add_argument("model", type=Path)
    parser.add_argument("--view", type=Path, action="append", required=True, dest="views")
    parser.add_argument("--child-view", type=Path, action="append", dest="child_views")
    parser.add_argument("--view-set", type=Path, required=True)
    parser.add_argument("--explain-pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="必须位于项目仓库之外")
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    outputs = [args.output, *([args.markdown_output] if args.markdown_output else [])]
    if _inside_repository(args.output):
        print("Codex Fragment 必须输出到项目仓库之外的线程可视化目录。", file=sys.stderr)
        return 2
    if any(path.exists() for path in outputs) and not args.overwrite:
        print("Codex Fragment 或 Markdown 输出已存在。", file=sys.stderr)
        return 2
    try:
        panorama, _ = load_panorama(args.panorama)
        model = _load(args.model)
        views = [_load(path) for path in args.views]
        child_views = [_load(path) for path in (args.child_views or [])]
        view_set = _load(args.view_set)
        pack = _load(args.explain_pack)
        errors = validate_explain_pack(
            pack, panorama, model, views, view_set, child_views=child_views
        )
        if errors:
            raise PanoramaExplainPackError("Explain Pack 无效：" + "; ".join(errors[:20]))
        fragment = render_fragment(pack, [*views, *child_views])
        atomic_write(args.output, fragment)
        if args.markdown_output:
            atomic_write(args.markdown_output, render_markdown(pack))
    except (OSError, UnicodeError, ValidationRuntimeError, PanoramaExplainPackError, PanoramaViewIRError) as exc:
        print(f"Codex Fragment 生成失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "rendered", "output": str(args.output), "bytes": len(fragment.encode("utf-8")), "explainPackId": pack["explainPackId"], "markdownFallback": str(args.markdown_output) if args.markdown_output else None}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
