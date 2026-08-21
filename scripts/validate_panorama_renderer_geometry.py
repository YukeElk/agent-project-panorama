"""Validate deterministic multi-view renderer geometry without a browser."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from panorama_cli import ChineseArgumentParser
from panorama_io import atomic_write
from panorama_view_ir import PanoramaViewIRError, validate_model_ir, validate_view_ir


NODE_W = 190.0
NODE_H = 74.0


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PanoramaViewIRError(f"JSON 输入无效 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise PanoramaViewIRError(f"JSON 输入必须是 object：{path}")
    return value


def _general_layout(view: dict[str, Any]) -> dict[str, tuple[float, float]]:
    nodes = view["nodes"]
    ids = {node["id"] for node in nodes}
    rank = {node_id: 0 for node_id in ids}
    incoming = {node_id: 0 for node_id in ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in view["edges"]:
        source, target = edge["fromNodeId"], edge["toNodeId"]
        if source in ids and target in ids and source != target:
            incoming[target] += 1
            outgoing[source].append(target)
    queue = sorted(node_id for node_id in ids if incoming[node_id] == 0)
    seen: set[str] = set()
    while queue:
        node_id = queue.pop(0)
        seen.add(node_id)
        for target in sorted(outgoing[node_id]):
            rank[target] = max(rank[target], rank[node_id] + 1)
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
        queue.sort()
    for index, node_id in enumerate(sorted(ids - seen)):
        rank[node_id] = index % 2
    buckets: dict[int, list[dict[str, Any]]] = {}
    for node in nodes:
        buckets.setdefault(rank[node["id"]], []).append(node)
    positions: dict[str, tuple[float, float]] = {}
    for column, bucket in buckets.items():
        for row, node in enumerate(sorted(bucket, key=lambda item: (item["label"], item["id"]))):
            positions[node["id"]] = (65.0 + column * 255.0, 55.0 + row * 122.0)
    return positions


def _sequence_layout(view: dict[str, Any]) -> dict[str, tuple[float, float]]:
    return {
        node["id"]: (60.0 + index * 205.0, 42.0)
        for index, node in enumerate(view["nodes"])
    }


def _route(
    source: tuple[float, float],
    target: tuple[float, float],
    *,
    obstacles: list[tuple[float, float, float, float]],
    lane_index: int,
    lane_base: float,
) -> list[tuple[float, float]]:
    x1, y1 = source[0] + NODE_W, source[1] + NODE_H / 2
    x2, y2 = target[0], target[1] + NODE_H / 2
    if x2 >= x1 + 45:
        middle = (x1 + x2) / 2
        preferred = [(x1, y1), (middle, y1), (middle, y2), (x2, y2)]
    else:
        bend_y = max(source[1] + NODE_H, target[1] + NODE_H) + 32
        preferred = [
            (x1, y1),
            (x1 + 28, y1),
            (x1 + 28, bend_y),
            (x2 - 28, bend_y),
            (x2 - 28, y2),
            (x2, y2),
        ]
    if not any(
        _segment_hits_rect(preferred[index - 1], preferred[index], rect)
        for rect in obstacles
        for index in range(1, len(preferred))
    ):
        return preferred
    lane_y = lane_base + lane_index * 18.0
    return [
        (x1, y1),
        (x1 + 28, y1),
        (x1 + 28, lane_y),
        (x2 - 28, lane_y),
        (x2 - 28, y2),
        (x2, y2),
    ]


def _rectangles(
    positions: dict[str, tuple[float, float]], *, sequence: bool
) -> dict[str, tuple[float, float, float, float]]:
    width, height = (150.0, 54.0) if sequence else (NODE_W, NODE_H)
    return {
        node_id: (position[0], position[1], width, height)
        for node_id, position in positions.items()
    }


def _overlap(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return (
        min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]) > 2
        and min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]) > 2
    )


def _segment_hits_rect(
    a: tuple[float, float], b: tuple[float, float], rect: tuple[float, ...]
) -> bool:
    x, y, width, height = rect
    if abs(a[1] - b[1]) < 0.1:
        return (
            y + 3 < a[1] < y + height - 3
            and max(min(a[0], b[0]), x + 3)
            < min(max(a[0], b[0]), x + width - 3)
        )
    return (
        x + 3 < a[0] < x + width - 3
        and max(min(a[1], b[1]), y + 3)
        < min(max(a[1], b[1]), y + height - 3)
    )


def validate_geometry(view: dict[str, Any]) -> dict[str, Any]:
    sequence = view["profile"] == "sequence"
    positions = _sequence_layout(view) if sequence else _general_layout(view)
    rects = _rectangles(positions, sequence=sequence)
    lane_base = max((rect[1] + rect[3] for rect in rects.values()), default=0.0) + 32.0
    findings: list[dict[str, Any]] = []
    node_ids = sorted(rects)
    for index, source in enumerate(node_ids):
        for target in node_ids[index + 1 :]:
            if _overlap(rects[source], rects[target]):
                findings.append(
                    {"code": "GEOM_NODE_OVERLAP", "nodeIds": [source, target]}
                )
    for order, edge in enumerate(view["edges"]):
        source, target = edge["fromNodeId"], edge["toNodeId"]
        if source not in positions or target not in positions:
            continue
        if sequence:
            y = 135.0 + order * 58.0
            points = [(positions[source][0] + 75.0, y), (positions[target][0] + 75.0, y)]
        else:
            points = _route(
                positions[source],
                positions[target],
                obstacles=[
                    rect
                    for node_id, rect in rects.items()
                    if node_id not in {source, target}
                ],
                lane_index=order,
                lane_base=lane_base,
            )
        for node_id, rect in rects.items():
            if node_id in {source, target}:
                continue
            if any(
                _segment_hits_rect(points[index - 1], points[index], rect)
                for index in range(1, len(points))
            ):
                findings.append(
                    {
                        "code": "GEOM_EDGE_THROUGH_NODE",
                        "edgeId": edge["id"],
                        "nodeId": node_id,
                    }
                )
    return {
        "viewId": view["viewId"],
        "profile": view["profile"],
        "status": "passed" if not findings else "failed",
        "nodeCount": len(view["nodes"]),
        "edgeCount": len(view["edges"]),
        "findings": findings,
    }


def validate_candidate(
    model: dict[str, Any], views: list[dict[str, Any]]
) -> dict[str, Any]:
    errors = validate_model_ir(model)
    if errors:
        raise PanoramaViewIRError("Model IR 无效：" + "; ".join(errors[:20]))
    results = []
    for view in views:
        errors = validate_view_ir(view, model)
        if errors:
            raise PanoramaViewIRError(
                f"View {view.get('viewId', '?')} 无效：" + "; ".join(errors[:20])
            )
        results.append(validate_geometry(view))
    return {
        "formatVersion": "panorama-renderer-geometry-validation.v0.1",
        "status": "passed"
        if all(result["status"] == "passed" for result in results)
        else "failed",
        "modelId": model["modelId"],
        "modelSemanticHash": model["integrity"]["semanticHash"],
        "views": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="独立检查 Renderer 候选的节点重叠和边穿节点。")
    parser.add_argument("model", type=Path)
    parser.add_argument("--view", type=Path, action="append", required=True, dest="views")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = validate_candidate(_load(args.model), [_load(path) for path in args.views])
    except PanoramaViewIRError as exc:
        print(f"Renderer Geometry 校验失败：{exc}", file=sys.stderr)
        return 2
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        atomic_write(args.output, payload)
    print(payload, end="")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
