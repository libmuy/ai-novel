# -*- coding: utf-8 -*-
"""共享路径常量。纯 Path 运算，不依赖 sys.path 是否已插好，各模块可直接 import。"""
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent   # 01_小说通用工具/
SYS_DIR = TOOLS_DIR.parent / "00_系统级"
REPO_ROOT = TOOLS_DIR.parents[1]
UI_DIR = TOOLS_DIR / "serve_ui"
