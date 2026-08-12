from __future__ import annotations

from pathlib import Path

from panorama_io import compute_data_hash, compute_presentation_hash, extract_data, replace_data
from panorama_cli import ChineseArgumentParser
from upgrade_renderer import RendererUpgradeError


def test_renderer_exposes_complete_zh_cn_surface(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    assert 'var RENDERER_VERSION = "0.2.0"' in source
    assert 'var UI_LOCALE = "zh-CN"' in source
    assert "var ZH_LABELS" in source
    assert source.index("var ZH_LABELS") < source.index("renderAll();")
    for text in (
        "控制台",
        "系统",
        "演进",
        "逻辑架构",
        "运行时与资源",
        "架构来源",
        "当前",
        "目标",
        "迁移",
        "资源池",
        "复制",
        "关闭详情",
    ):
        assert text in source


def test_renderer_keeps_machine_protocol_in_english(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    for token in (
        'SUPPORTED_SCHEMA_VERSIONS = ["0.1", "0.2"]',
        'data-view="control"',
        'data-system-view="runtime"',
        'state.architectureMode === "transition"',
        'credentials.mode === "external_store"',
        'case "decision"',
    ):
        assert token in source


def test_renderer_has_no_known_english_only_ui_labels(template_path: Path):
    source = template_path.read_text(encoding="utf-8")

    for text in (
        ">CONTROL<",
        ">SYSTEM<",
        ">EVOLUTION<",
        ">Logical Architecture<",
        ">Runtime &amp; Resources<",
        ">Architecture Source<",
        ">Deployment View<",
        ">Resource Pool<",
        ">Copy<",
        "No reviewed update.",
        "No guidance.",
        "Nothing to copy",
        "Copied to clipboard",
    ):
        assert text not in source


def test_cli_help_framework_is_chinese():
    parser = ChineseArgumentParser(description="测试命令")
    parser.add_argument("source", help="源文件")
    help_text = parser.format_help()

    assert help_text.startswith("用法:")
    assert "位置参数:" in help_text
    assert "选项:" in help_text
    assert "显示帮助并退出" in help_text


def test_renderer_upgrade_preserves_data_and_replaces_presentation(
    project_root: Path,
    template_path: Path,
    reference_data: dict,
    tmp_path: Path,
):
    from upgrade_renderer import upgrade_renderer

    legacy_template = tmp_path / "legacy-template.html"
    legacy_template.write_text(
        template_path.read_text(encoding="utf-8")
        .replace('lang="zh-CN"', 'lang="en"', 1)
        .replace(
            'var RENDERER_VERSION = "0.1.2";',
            'var RENDERER_VERSION = "0.1.1";',
            1,
        )
        .replace("控制台", "CONTROL", 1),
        encoding="utf-8",
    )
    source = tmp_path / "legacy.html"
    replace_data(legacy_template, reference_data, source)
    original_data_hash = compute_data_hash(extract_data(source))
    original_presentation_hash = compute_presentation_hash(
        source.read_text(encoding="utf-8")
    )

    output = tmp_path / "localized.html"
    result = upgrade_renderer(source, template_path, output)

    assert result == output
    assert compute_data_hash(extract_data(output)) == original_data_hash
    assert compute_presentation_hash(output.read_text(encoding="utf-8")) == (
        compute_presentation_hash(template_path.read_text(encoding="utf-8"))
    )
    assert compute_presentation_hash(output.read_text(encoding="utf-8")) != (
        original_presentation_hash
    )


def test_renderer_upgrade_requires_explicit_overwrite_for_source_path(
    template_path: Path,
    reference_data: dict,
    tmp_path: Path,
):
    from upgrade_renderer import upgrade_renderer

    source = tmp_path / "project.html"
    replace_data(template_path, reference_data, source)

    try:
        upgrade_renderer(source, template_path, source)
    except RendererUpgradeError:
        pass
    else:
        raise AssertionError("原位升级必须显式授权 overwrite")
