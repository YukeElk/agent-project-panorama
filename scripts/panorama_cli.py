"""Panorama 命令行的简体中文 argparse 外壳。"""

from __future__ import annotations

import argparse
import sys


class ChineseArgumentParser(argparse.ArgumentParser):
    """保留英文 Flag，同时中文化 argparse 的用户可见框架文字。"""

    def format_help(self) -> str:
        return (
            super()
            .format_help()
            .replace("usage:", "用法:", 1)
            .replace("positional arguments:", "位置参数:")
            .replace("options:", "选项:")
            .replace("show this help message and exit", "显示帮助并退出")
        )

    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "用法:", 1)

    def error(self, message: str) -> None:
        message = message.replace(
            "the following arguments are required:", "缺少必需参数："
        ).replace("unrecognized arguments:", "无法识别的参数：")
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: 错误：{message}\n")
