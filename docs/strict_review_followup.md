# 严格审查后续整改（2026-10-25，草稿审阅）

本轮基线为 main `ecf0bc5`。与工程报告审查版本 `965ba87` 比较，两个后续提交仅增删报告文件，源代码相同。旧文档中的「32 已修 / 282 测试通过」是历史记录，不能作为本轮修改或科学有效性的验收结论。

## 已实现并进行本地验收

1. CellRank 回退抽到 `cellflow/absorption.py`：按实际非空终末类别聚合边界矩阵，保留类别顺序，拒绝无终态、不可达瞬态、非随机转移矩阵、超限稠密求解及残差/概率/行和越界。容差 `1e-8`，只修正容差内舍入误差，不归一化掩盖错误。原生 GPCCA 返回与回退求解都独立按原始转移矩阵检查吸收方程残差、终末边界及可达性，诊断写入结果；稀疏显式零先移除，不作为图边。终末概率维度、非有限值、范围、行和属于硬质量门禁。
2. CSV 大小文件路径共用严格表达值检查，拒绝缺失、文本和无穷值（含 float32 溢出）。不再将含坏值的基因列悄悄当元数据。显式元数据列名为 group、cluster、batch、sample、cell_type、celltype、condition、donor、barcode、cell_id、label、cell、time、assigned_cluster；其他文本列默认拒绝，请将任意复杂元数据放入 h5ad 的 obs。分块读取元数据空值保留 None，并保证异常时关闭读取器。公开数据 CSV 转换也不再填零。
3. PID 创建时间未知/无效时不杀进程，修正 64 位 Windows HANDLE 声明，身份容差从两秒缩至微秒。Windows 每任务具名 Job Object，wrapper 先加入受控对象、完成握手，再启动 CLI；子孙默认继承，禁止 breakaway，关闭父持有句柄自动终止任务树。取消不杀 supervisor；缺少 Job Object 的旧任务拒绝强杀，等待 supervisor 收尾或人工核验。未确认退出不释放运行名额；访问拒绝/创建时间未知时保留 supervisor 与名额，防止重复调度，可能需要人工核验旧任务。
4. veloVI `converged=null`、`convergence_status=not_assessed`，独立记录是否耗尽训练预算及损失历史；非有限损失拒绝。后验均值不是零方差：形状不符或采样不可用时不输出不确定性文件，记录原因；样本标准差验证维度和有限性。这些仍不是收敛性或生物学正确性的证明。
5. 新增已知解析解的多终末类别回归；增加真实 h5ad 只读 QC、官方胰腺发育数据双种子验收入口。生产 Qt 壳 + 已构建 React 的五视图点击/布局/截图、通过真实导入 API 注册明确标为模拟的夹具、在 UI 勾选原始计数并点击运行、轮询真实队列至完成且质量通过，均有自动化检查。`tests/test_production_ui.py` 已接入原有 CI 的 unittest 发现流程，无需修改工作流即可门禁。额外的 CI 截图/日志 artifact 上传步骤受 OAuth workflow scope 限制，补丁仅保留本地。当前 E2E 不覆盖系统文件选择器、长任务取消按钮、报告下载和多屏 DPI。
6. 任务列表/详情/改名/删除/取消及下载状态/取消检查当前项目；数据检查与提交还必须属于当前项目可见数据集，不能仅凭 results 下的路径绕过。公开库的隐藏状态按项目保存，兼容旧全局列表到 default 项目；关联移除不删除源文件。单机项目作用域不构成多用户鉴权。
7. HTML CSP 移除显式 `file:` 图片许可；清洗器拒绝 file URL、图片绝对路径、父目录遍历及远程协议，保留本地相对图片和受限 data 图片。旧报告不自动改写。

## 证据与边界

- 本地完整测试 300 项通过、无跳过（220.025 秒），包含实际 veloVI 隔离环境短训练和生产 Qt/React E2E。收尾新增稀疏显式零/未知进程身份保护后，受影响的数值、整个队列生命周期、项目隔离、CellRank 集成、质量门禁与 UI 共 45 项复验通过（76.037 秒）。不要把两轮数量相加当成唯一测试数。
- `npm ci`、TypeScript 类型检查、Vite 构建通过；编译、重复定义及文档链接检查通过。前端源码未因本轮修复替换框架。
- 真实 GSE84133 本地 h5ad 的 QC/报告链路 completed，源文件前后 SHA256 均为 `6061119c677861e0ab8eb3267d97c0af4397bba3cdfdb95a0196ee0a8dd6c724`。这不验证速度、真实终末谱系或泛化。
- 实际短训练验证了 veloVI 输入 Ms/Mu、损失历史有限、`converged=null` 和训练预算字段。它只证明接口/诊断契约，不证明训练收敛。
- 官方胰腺发育数据下载三次均未得到完整文件，最后有界 curl 下载以退出码 28（超时）结束；截断文件没有进入计算，双种子真实速度/多终态验收仍未验证。脚本 `tools/acceptance_pancreas.py` 固定按标注群最多抽 75 个真实细胞、抽样种子 42，分析种子 42/43、1000 HVG、30 邻居、5 个宏态；不为获得多终态反复调参。
- 旧提交的两次 CI 失败已修复，`04ea755` 的前后端 CI 成功；收尾提交的 CI 以 PR Checks 对应的 head SHA 为准，不能把旧提交成功当成新提交通过。
- `python -m tools.acceptance_real_data --input <real.h5ad> --output <new-directory> --source <accession>` 校验源文件前后 SHA256；只验证真实表达数据 QC/报告链路，绝不虚构剪接层或速度。数据不进 Git。
- `python -m tools.acceptance_ui --output .tmp/ui-acceptance` 使用独立空闲本机端口和生产桌面视图；不接管现有服务。可设置 `QT_QPA_PLATFORM=offscreen` 用于 CI。
- 仍需真实剪接数据上多终态 CellRank、多个种子/参数、已知生物谱系对照、足够轮数的 veloVI 训练、模型诊断复核、长任务/OOM 和原生多屏 DPI 验收。模拟解析解只证明数值契约。
- 当前交付状态：**工程修复可在草稿 PR 审阅；不可宣称完整科学验证或无条件外部交付**。真实多终态速度基准、足够轮数的 veloVI 收敛评估、已知谱系对照仍缺证据。
- 适用范围仍是 127.0.0.1 单机单用户；项目隔离不构成多租户安全边界。多人/远程部署需要认证授权、并发安全存储及资源调度；GPU 任务需要专门的资源准入/worker，不由当前单机队列承诺。

## 实现依据

- [CellRank 官方命运概率教程](https://cellrank.readthedocs.io/en/latest/notebooks/tutorials/estimators/700_fate_probabilities.html)：概率对应终末群体。
- [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)：进程树继承、具名对象和 KILL_ON_JOB_CLOSE。
- [veloVI 官方 API](https://velovi.readthedocs.io/en/latest/api/reference/velovi.VELOVI.html)：区分后验采样与均值。

保留 FastAPI / React / Qt / 子进程队列架构。只提取数值求解、后验诊断、Windows 进程边界三个小模块，不做整体框架替换。
