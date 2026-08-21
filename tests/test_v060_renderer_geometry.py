from __future__ import annotations

from test_v060_multi_view_renderer import _model_and_views
from validate_panorama_renderer_geometry import validate_candidate, validate_geometry


def test_v060_six_view_geometry_candidate_passes(project_root, tmp_path):
    model, views = _model_and_views(project_root, tmp_path)
    result = validate_candidate(model, views)

    assert result["status"] == "passed"
    assert len(result["views"]) == 6
    assert all(view["findings"] == [] for view in result["views"])


def test_v060_geometry_detects_edge_through_intermediate_node():
    view = {
        "viewId": "VIEW-GEOMETRY-FAILURE",
        "profile": "module",
        "nodes": [
            {"id": "NODE-A", "label": "A"},
            {"id": "NODE-B", "label": "B"},
            {"id": "NODE-D", "label": "D"},
        ],
        "edges": [
            {"id": "EDGE-A-D", "fromNodeId": "NODE-A", "toNodeId": "NODE-D"},
            {"id": "EDGE-D-B", "fromNodeId": "NODE-D", "toNodeId": "NODE-B"},
            {"id": "EDGE-A-B", "fromNodeId": "NODE-A", "toNodeId": "NODE-B"},
        ],
    }

    result = validate_geometry(view)

    assert result["status"] == "failed"
    assert {
        "code": "GEOM_EDGE_THROUGH_NODE",
        "edgeId": "EDGE-A-B",
        "nodeId": "NODE-D",
    } in result["findings"]
