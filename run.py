#!/usr/bin/env python3
"""
Apple Music 歌单导入工具入口脚本
运行示例:
  python run.py config               # 配置 Apple Music 认证
  python run.py sync <歌单链接/路径>  # 命令行导入歌单
  python run.py web                  # 启动本地可视化网页界面
  python run.py search <歌曲名>      # 搜索 Apple Music 曲库
"""

import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from applemusic.cli import app

if __name__ == "__main__":
    app()
