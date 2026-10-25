"""上下文帮助：页面说明、参数提示、可收起帮助面板。

帮助内容只描述软件真实具备的能力与限制。
"""
from __future__ import annotations

import streamlit as st

PAGES = {
    'overview': dict(
        purpose='查看项目状态：数据集、任务与流程位置。',
        next='先在「数据」确认数据可用，再到「模型分析」运行方法。',
        notes=['任务在后台独立运行，切换页面不会中断。',
               '结果保存在 results/ 下，一次任务一个文件夹。'],
    ),
    'data': dict(
        purpose='导入数据，并检查它是否满足各模型的前提。',
        next='数据可用后进入「模型分析」。',
        notes=['支持的格式：.h5ad / .loom / 10x / CSV / TSV / zip；方向自动判断。',
               '原始计数与剪接层需要你核实来源后勾选声明。',
               '普通表达矩阵不能做 RNA velocity。'],
    ),
    'model': dict(
        purpose='选择方法、确认前提、配置参数并提交本机任务。',
        next='提交后到「分析结果」查看产出。',
        notes=['「可运行」= 有适配器且本机依赖齐全。',
               '前置条件不满足时会写明缺什么，不会自动换方法。',
               '不同模型的似然、损失、时间尺度不可直接比较。'],
    ),
    'results': dict(
        purpose='查看一次任务的实际产出：图、指标、基因表与运行记录。',
        next='确认结果后到「报告」导出 A/B 报告。',
        notes=['箭头与拟时序是模型推断，不是实验验证。',
               '图与指标只对应当前选中的任务。'],
    ),
    'reports': dict(
        purpose='把本次任务整理成 A 生物学报告与 B 技术与复现报告。',
        next='预览后下载 Markdown / HTML / ZIP。',
        notes=['报告会包含本机路径与样本 ID，外发前请审阅。'],
    ),
}


def panel(key):
    info = PAGES.get(key)
    if not info or not st.session_state.get('help_on'):
        return
    with st.container(border=True):
        st.markdown('**本页说明**')
        st.markdown(f'<div class="help">{info["purpose"]}</div>', unsafe_allow_html=True)
        st.markdown('<div class="help"><b>下一步</b></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="help">{info["next"]}</div>', unsafe_allow_html=True)
        st.markdown('<div class="help"><b>注意</b></div>', unsafe_allow_html=True)
        st.markdown('<div class="help">' + ''.join(f'· {n}<br>' for n in info['notes']) + '</div>',
                    unsafe_allow_html=True)


def layout():
    if st.session_state.get('help_on'):
        main, side = st.columns([3.4, 1.05], gap='large')
        return main, side
    return st.container(), None


@st.dialog('使用说明', width='large')
def about():
    st.markdown("""
**流程**：数据 → 模型分析 → 分析结果 → 报告。

**能做什么**：调用 scVelo（deterministic / stochastic / dynamical）、veloVI、
CellRank、DPT、PAGA、Leiden 等方法，输出图形、指标与两份中文报告。

**数据位置**：结果 `results/<任务ID>/`；队列 `results/queue.sqlite`；
导入副本 `results/inputs/`；公开数据 `data/public/`。

**边界**：不做细胞类型注释与富集；不做方法排名；箭头、拟时序、命运概率均为模型推断。

**帮助**：参数旁的小问号解释含义；侧边栏「上下文帮助」显示本页说明。
""")
