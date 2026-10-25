#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跟随 DSH 启动挂件（源码模式的入口）。

真正的循环在 deepseek_balance_widget.follow_main()：
- DSH（DeepSeek Harness.exe）启动而挂件没在跑时，把挂件拉起来；
- `--restart`：每次检测到 DSH 新启动都先结束旧挂件再重开（开发调试用）；
- `--debug`：状态写进 app.log；
- `--force`：忽略「跟随 DSH 启动」开关，强制常驻（手工测试用）；
- 常驻后台，无窗口；由挂件右键菜单的「跟随 DSH 启动」写进 HKCU 的 Run 键；
- 关闭开关时挂件会 SetEvent 通知本进程退出（最多 2 秒内）。

打包后的 exe 用 `DeepSeekBalance.exe --follow` 走同一条逻辑，
所以这里不再重复实现，只做入口转发。

    pythonw tools\\follow_dsh.py             # 常驻跟随
    python  tools\\follow_dsh.py --restart   # 每次 DSH 启动都重启挂件
    python  tools\\follow_dsh.py --debug     # 状态写进 app.log
    python  tools\\follow_dsh.py --force     # 忽略开关，强制常驻
"""

import sys
from pathlib import Path

# 让本脚本在 tools\ 子目录里也能 import 到上级目录的挂件模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepseek_balance_widget import follow_main  # noqa: E402

if __name__ == "__main__":
    try:
        sys.exit(follow_main(debug="--debug" in sys.argv,
                             restart="--restart" in sys.argv,
                             force="--force" in sys.argv))
    except KeyboardInterrupt:
        sys.exit(0)
