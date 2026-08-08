from __future__ import annotations


def test_renderer_has_unified_safe_href(template_path):
    source = template_path.read_text(encoding="utf-8")
    assert "function safeHref(" in source
    assert "javascript:" in source
    assert "function safeAnchor(" in source


def test_all_dynamic_links_use_safe_anchor(template_path):
    source = template_path.read_text(encoding="utf-8")
    assert "<a href='\" + e(endpoint)" not in source
    assert source.count("<a href='\" + e(href)") == 1  # safeAnchor implementation only


def test_runtime_supports_deployment_list_and_resource_pool(template_path):
    source = template_path.read_text(encoding="utf-8")
    assert "selectedDeploymentId" in source
    assert "部署视图" in source
    assert "资源池" in source
    assert "resourcePoolFilters" in source


def test_generic_entity_inspector_contract(template_path):
    source = template_path.read_text(encoding="utf-8")
    assert "function renderEntityInspector(" in source
    for entity_type in ("decision", "acceptance", "gate", "risk", "connection", "reference"):
        assert f'case "{entity_type}"' in source


def test_renderer_declares_compatibility_contract(template_path):
    source = template_path.read_text(encoding="utf-8")
    assert "SUPPORTED_SCHEMA_VERSIONS" in source
    assert "不支持的 Panorama Schema" in source
