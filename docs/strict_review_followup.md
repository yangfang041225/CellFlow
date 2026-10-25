# 严格审查后续整改（2026-10-25，尚未交付）

本轮基线为 main `ecf0bc5`。与工程报告审查版本 `965ba87` 比较，两个后续提交仅增删报告文件，源代码相同。旧文档中的「32 已修 / 282 测试通过」是历史记录，不能作为本轮修改或科学有效性的验收结论。

## 已实现，等待全量验收

1. CellRank 回退抽到 `cellflow/absorption.py`：按实际非空终末类别聚合边界矩阵，保留类别顺序，拒绝无终态、不可达瞬态、非随机转移矩阵、超限稠密求解及残差/概率/行和越界。容差 `1e-8`，只修正容差内舍入误差，不归一化掩盖错误。回退诊断写入结果；原生 GPCCA 未暴露的残差明确标记为未提供。终末概率维度、非有限值、范围、行和属于硬质量门禁。
2. CSV 大小文件路径共用严格表达值检查，拒绝缺失、文本和无穷值（含 float32 溢出）。不再将含坏值的基因列悄悄当元数据。显式元数据列名为 group、cluster、batch、sample、cell_type、celltype、condition、donor、barcode、cell_id、label、cell、time、assigned_cluster；其他文本列默认拒绝，请将任意复杂元数据放入 h5ad 的 obs。分块读取元数据空值保留 None，并保证异常时关闭读取器。公开数据 CSV 转换也不再填零。
3. PID 创建时间未知/无效时不杀进程，修正 64 位 Windows HANDLE 声明，身份容差从两秒缩至微秒。Windows 每任务具名 Job Object，wrapper 先加入受控对象、完成握手，再启动 CLI；子孙默认继承，禁止 breakaway，关闭父持有句柄自动终止任务树。取消不杀 supervisor；缺少 Job Object 的旧任务拒绝强杀，等待 supervisor 收尾或人工核验。未确认退出不释放运行名额。
4. veloVI `converged=null`、`convergence_status=not_assessed`，独立记录是否耗尽训练预算及损失历史；非有限损失拒绝。后验均值不是零方差：形状不符或采样不可用时不输出不确定性文件，记录原因；样本标准差验证维度和有限性。这些仍不是收敛性或生物学正确性的证明。
5. 新增已知解析解的多终末类别回归；增加真实 h5ad 只读 QC 验收入口，以及生产 Qt 壳 + 已构建 React 五视图点击/布局/截图验收入口。已准备 CI Qt/React 检查和证据上传补丁，但当前 OAuth 凭据缺少 workflow scope，GitHub 拒绝推送工作流；该补丁仅保留本地，PR 尚不含此 CI 变更。导航验收不等于覆盖文件选择器、长任务取消、报告下载等完整用户旅程。
6. 任务详情/改名/删除/取消及下载状态/取消检查当前项目。公开库的隐藏状态按项目保存，兼容旧全局列表到 default 项目；关联移除不删除源文件。单机项目作用域不构成多用户鉴权。
7. HTML CSP 移除显式 `file:` 图片许可；清洗器拒绝 file URL、图片绝对路径、父目录遍历及远程协议，保留本地相对图片和受限 data 图片。旧报告不自动改写。

## 证据与边界

- 定向数值/CSV/后验/质量检查 15 项通过；另有真实 Windows wrapper 记录和取消后继续排队的定向检查通过。全量后端、前端构建、真实数据及 Qt 检查结果必须以本轮验收输出为准，当前待运行。
- `python -m tools.acceptance_real_data --input <real.h5ad> --output <new-directory> --source <accession>` 校验源文件前后 SHA256；只验证真实表达数据 QC/报告链路，绝不虚构剪接层或速度。数据不进 Git。
- `python -m tools.acceptance_ui --output .tmp/ui-acceptance` 使用独立空闲本机端口和生产桌面视图；不接管现有服务。可设置 `QT_QPA_PLATFORM=offscreen` 用于 CI。
- 仍需真实剪接数据上多终态 CellRank、多个种子/参数、已知生物谱系对照、足够轮数的 veloVI 训练、模型诊断复核、长任务/OOM 和原生多屏 DPI 验收。模拟解析解只证明数值契约。
- 当前交付状态：**不可宣布整改完成，也不可宣称已通过外部科研试用验收**。仅在验证证据齐备、明确探索性用途后评估有条件试用。

## 实现依据

- [CellRank 官方命运概率教程](https://cellrank.readthedocs.io/en/latest/notebooks/tutorials/estimators/700_fate_probabilities.html)：概率对应终末群体。
- [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)：进程树继承、具名对象和 KILL_ON_JOB_CLOSE。
- [veloVI 官方 API](https://velovi.readthedocs.io/en/latest/api/reference/velovi.VELOVI.html)：区分后验采样与均值。

保留 FastAPI / React / Qt / 子进程队列架构。只提取数值求解、后验诊断、Windows 进程边界三个小模块，不做整体框架替换。
