# Phase II · Windows 可靠性验收记录（P2 部分）

日期：2026-10-26
分支：`phase2/scientific-validation`
结论：**可自动化的进程内场景已通过；“干净机器安装 / 强制关窗 / 系统磁盘写满 / 多屏 DPI”仍未在独立机器上完成。**

---

## 1. 已自动验证

| 场景 | 证据 | 结果 |
|---|---|---|
| 连续两个任务 | `benchmarks/windows_reliability.py`：两个 QC 任务顺序提交并完成 | 通过 |
| 完成后无 running 残留 | 同上：终态后 `state='running'` 计数为 0 | 通过 |
| 损坏输入 | 同上：非 HDF5 的 `bad.h5ad` → `failed`，错误可读 | 通过 |
| 终态唯一 | 同上：任务不会同时 running/completed | 通过 |
| 取消任务/进程树 | `tests/test_queue_lifecycle.py`（24 项，含父子进程、孤儿、PID 复用） | 通过 |
| 并发上限 | 队列生命周期测试：两个不同任务峰值 ≤ 配置上限 | 通过 |
| 重启恢复/心跳 | 队列生命周期测试：drain 启动恢复、租约超时、interrupted 区分 | 通过 |
| 准备失败留档 | 队列生命周期测试：配置写入失败/损坏/解释器缺失 | 通过 |
| 内存不足预检 | `tests/test_memory.py` + `test_quality_safety` | 通过 |
| 磁盘不足 | 新增 `tests/test_disk_guard.py`：磁盘余量 <1 GB 时任务明确 failed，不写 result | 通过 |
| 环境缺失 | `tests/test_registry_health.py`、`tests/test_install_checks.py` | 通过 |
| Qt 关窗释放端口 | `tests/test_desktop.py`（真实子进程 API 启动/停止/端口释放） | 通过 |
| 原生窗口关闭 | 早前在 1180×780 Qt 窗口实测：`WINDOW_CLOSED` → `API 已停止` → 8600 释放（同一桌面代码路径） | 记录，未在本分支重复 |

复跑：

```powershell
.venv\Scripts\python.exe -m benchmarks.windows_reliability
.venv\Scripts\python.exe -m unittest tests.test_queue_lifecycle tests.test_disk_guard tests.test_desktop -v
```

本机结果（2026-10-26）：

```json
{"ok": true, "checks": ["two_sequential_jobs", "no_running_after_completion",
 "corrupt_input_fails_cleanly", "terminal_states_are_single"]}
```

---

## 2. 尚未完成（不得声称通过）

| 场景 | 状态 | 原因 |
|---|---|---|
| 干净 Windows 机器安装 | ❌ 未验证 | 需要独立机器/虚拟机；本机复用已有 `.venv` |
| 普通用户权限（非管理员）全流程 | ❌ 未验证 | 需要标准用户账户测试 |
| 强制结束 Qt 进程后的孤儿进程检查 | ⚠️ 部分 | 已测正常关窗；强制杀进程后的 API/子进程清理未在本分支重测 |
| 系统磁盘真正写满 | ⚠️ 模拟 | 仅用门禁单元测试模拟低磁盘，未填满真实磁盘 |
| 内存 OOM | ⚠️ 模拟 | 只用预检拒绝，未做真实 OOM 破坏性测试 |
| 多屏 125/150/200% DPI | ❌ 未验证 | 需要多显示器/原生缩放环境 |
| 卸载与数据保留 | ❌ 未验证 | 没有安装器/卸载器 |

这些项目在 `docs/scientific_validation/phase2_final_report.md`（待写）中必须继续标为未验证，
不能因为本文件通过而升级为“已交付”。

---

## 3. 已修复的可靠性缺口（本轮）

1. **磁盘余量门禁**：`memory.require_disk` 在准备阶段检查输出磁盘 ≥1 GB；
   不足时任务进入 failed，不产生 `result.json` 或半成品报告。
2. **损坏输入**：不进入计算；任务失败并保留可读错误。
3. **连续任务**：两个任务先后完成，终态后无 running 残留。
