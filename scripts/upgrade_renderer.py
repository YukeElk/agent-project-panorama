"""受控升级 Panorama Single HTML 的 Presentation Layer。"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from panorama_cli import ChineseArgumentParser
from panorama_io import (
    compute_data_hash,
    compute_presentation_hash,
    create_backup,
    extract_data,
    replace_data,
)
from validate_panorama import validate_data


DEFAULT_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "panorama.html"
DEFAULT_SCHEMA = None


class RendererUpgradeError(RuntimeError):
    """Renderer 升级无法安全完成。"""


def upgrade_renderer(
    source: Path,
    template: Path,
    output: Path,
    schema: Path | None = DEFAULT_SCHEMA,
    *,
    overwrite: bool = False,
) -> Path:
    """只替换 Presentation Layer，并证明逻辑数据完全不变。"""

    source = source.resolve()
    template = template.resolve()
    output = output.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if not template.is_file():
        raise FileNotFoundError(template)
    if output == source and not overwrite:
        raise RendererUpgradeError("原位升级必须显式授权，并先创建备份。")
    if output.exists() and output != source and not overwrite:
        raise RendererUpgradeError(f"输出文件已存在：{output}")

    original_source_bytes = source.read_bytes()
    data = extract_data(source)
    before_hash = compute_data_hash(data)
    before_report = validate_data(
        data,
        schema,
        base_dir=source.parent,
        source_path=source,
    )
    if before_report.errors:
        messages = "\n".join(issue.render() for issue in before_report.errors)
        raise RendererUpgradeError(f"源 Panorama 校验失败：\n{messages}")

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".renderer-staged",
    )
    os.close(descriptor)
    staged = Path(staged_name)
    staged.unlink(missing_ok=True)
    try:
        replace_data(template, data, staged)
        upgraded_data = extract_data(staged)
        after_hash = compute_data_hash(upgraded_data)
        if after_hash != before_hash or upgraded_data != data:
            raise RendererUpgradeError("Renderer 升级改变了 Panorama 数据。")
        after_report = validate_data(
            upgraded_data,
            schema,
            base_dir=output.parent,
            source_path=staged,
        )
        if after_report.errors:
            messages = "\n".join(issue.render() for issue in after_report.errors)
            raise RendererUpgradeError(f"升级后的 Panorama 校验失败：\n{messages}")
        expected_presentation = compute_presentation_hash(
            template.read_text(encoding="utf-8")
        )
        actual_presentation = compute_presentation_hash(staged.read_text(encoding="utf-8"))
        if actual_presentation != expected_presentation:
            raise RendererUpgradeError("升级结果与目标 Renderer 的 Presentation Hash 不一致。")
        if output == source and source.read_bytes() != original_source_bytes:
            raise RendererUpgradeError(
                "源 Panorama 在 Renderer 校验期间发生变化；未覆盖并发更新。"
            )
        os.replace(staged, output)
    finally:
        staged.unlink(missing_ok=True)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="将现有 Panorama HTML 安全升级到当前中文 Renderer，保持数据不变。"
    )
    parser.add_argument("source", type=Path, help="待升级的 Panorama HTML")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="目标 Renderer 模板")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="数据校验 Schema")
    parser.add_argument("--output", type=Path, help="输出路径；默认生成 *.zh-CN.html")
    parser.add_argument("--in-place", action="store_true", help="原位升级，并先创建时间戳备份")
    parser.add_argument("--force", action="store_true", help="允许覆盖已有的非源输出文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.source.resolve()
    if args.in_place and args.output:
        print("错误：--in-place 与 --output 不能同时使用。", file=sys.stderr)
        return 2
    output = source if args.in_place else (
        args.output.resolve()
        if args.output
        else source.with_name(f"{source.stem}.zh-CN{source.suffix}")
    )
    if output == source and not args.in_place:
        print("错误：原位升级必须显式使用 --in-place，以确保先创建备份。", file=sys.stderr)
        return 2
    backup = None
    try:
        if args.in_place:
            backup = create_backup(source)
        result = upgrade_renderer(
            source,
            args.template,
            output,
            args.schema,
            overwrite=args.force or args.in_place,
        )
    except (OSError, ValueError, RendererUpgradeError) as exc:
        print(f"Renderer 升级失败：{exc}", file=sys.stderr)
        return 2
    print(f"Renderer 升级完成：{result}")
    print(f"数据 SHA-256：{compute_data_hash(extract_data(result))}")
    if backup:
        print(f"备份：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
