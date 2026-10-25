"""CellFlow：本地单细胞动力学工作台。

导入本包时先把 matplotlib 固定到 Agg 后端：
- 本工具不出图窗（界面用 plotly / 手写 SVG），不需要任何 GUI 后端；
- 在无显示环境或“主线程之外创建 Tk 对象”的情况下，默认 TkAgg 会导致
  `Tcl_AsyncDelete: async handler deleted by the wrong thread` 这类退出崩溃。
- 这个环境变量必须在 matplotlib 第一次被导入之前设置，所以放在包的 __init__ 里。
"""
import os

os.environ.setdefault('MPLBACKEND', 'Agg')

__version__ = '0.2.0'
