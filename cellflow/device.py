"""计算设备抽象：本机 CPU / 本机 CUDA / 将来连课题组服务器。

设计原则（对应用户要求：不要否定和局限本机功能）：
- 默认 `auto`：有可用的 CUDA + PyTorch 就用 GPU，否则用 CPU，**绝不因为“本机没有独显”写死 CPU**；
- 显式 `cuda`：如果环境不满足，报出**具体原因**（没装 PyTorch / 没装 CUDA 版 / 驱动不可用），而不是静默降级；
- 命令行/服务器上可以用环境变量 `CELLFLOW_DEVICE=cuda` 固定，不必改代码；
- 结果里记录“实际用了什么设备”，写进报告。

注意：目前只有**神经网络类**方法（RegVelo，将来的 veloVI/DeepVelo/UniTVelo/VeloAE）能真正吃到 GPU；
scVelo / scanpy / CellRank 的这部分计算是 CPU 实现，选 GPU 也不会更快——程序会如实说明。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict

GPU_CAPABLE = ('regvelo', 'velovi', 'deepvelo', 'unitvelo', 'veloae', 'dynamo')
VALID = ('auto', 'cpu', 'cuda')


@dataclass
class DeviceInfo:
    requested: str
    device: str          # 实际使用：'cpu' 或 'cuda'
    available: bool
    torch_version: str = ''
    cuda_version: str = ''
    gpu_names: tuple = ()
    reason: str = ''

    def as_dict(self) -> Dict[str, object]:
        return dict(requested=self.requested, device=self.device, available=self.available,
                    torch_version=self.torch_version, cuda_version=self.cuda_version,
                    gpu_names=list(self.gpu_names), reason=self.reason)


def inspect_environment() -> DeviceInfo:
    """检查当前解释器里有没有可用的 PyTorch + CUDA。"""
    try:
        import torch  # noqa: F401
    except Exception as exc:
        return DeviceInfo(requested='auto', device='cpu', available=False,
                          reason='当前环境没有安装 PyTorch（GPU 类方法需要它）；CPU 方法不受影响')
    import torch
    version = getattr(torch, '__version__', 'unknown')
    cuda_version = getattr(getattr(torch, 'version', None), 'cuda', None) or ''
    try:
        available = bool(torch.cuda.is_available())
    except Exception as exc:
        return DeviceInfo(requested='auto', device='cpu', available=False, torch_version=version,
                          reason='检查 CUDA 时出错：' + str(exc))
    names = ()
    if available:
        try:
            names = tuple(torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count()))
        except Exception:
            names = ('未知 GPU',)
    reason = '' if available else ('PyTorch 已安装，但这份 PyTorch 不带可用的 CUDA（可能是 CPU 版 wheel，'
                                   '或驱动/显卡不可用）')
    return DeviceInfo(requested='auto', device='cuda' if available else 'cpu',
                      available=available, torch_version=version,
                      cuda_version=str(cuda_version), gpu_names=names, reason=reason)


def resolve(requested: str = 'auto') -> DeviceInfo:
    """把用户的选择解析成实际设备；不满足条件时给出中文原因。"""
    requested = (requested or 'auto').strip().lower()
    env_override = (os.environ.get('CELLFLOW_DEVICE') or '').strip().lower()
    if env_override in ('cpu', 'cuda') and requested == 'auto':
        requested = env_override
    if requested not in VALID:
        requested = 'auto'
    info = inspect_environment()
    info.requested = requested
    if requested == 'cpu':
        info.device = 'cpu'
        info.reason = '按设置强制使用 CPU'
        return info
    if requested == 'cuda':
        if info.available:
            info.device = 'cuda'
            info.reason = '使用 GPU：' + '、'.join(info.gpu_names)
        else:
            info.device = 'cpu'
            info.reason = ('设置了 GPU，但不可用：' + (info.reason or '未知原因') +
                           '。已回退到 CPU；本次结果中会标明设备。')
        return info
    # auto
    if info.available:
        info.device = 'cuda'
        info.reason = '自动选择 GPU：' + '、'.join(info.gpu_names)
    else:
        info.device = 'cpu'
        info.reason = '自动选择 CPU：' + (info.reason or '没有检测到可用 GPU')
    return info


def note_for(method: str, device: str) -> str:
    """给某个方法一句设备说明（写进报告/日志）。"""
    if method in GPU_CAPABLE:
        if device == 'cuda':
            return '该方法支持 GPU：本次使用 CUDA。'
        return '该方法支持 GPU，但本次在 CPU 上运行（速度会慢一些；以后连到带 NVIDIA 的服务器可切 GPU）。'
    return '该方法的这部分计算由上游库以 CPU 实现，选 GPU 也不会更快。'
