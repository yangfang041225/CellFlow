"""测试包标记。

有这个文件，`python -m unittest tests.test_contracts` 才能解析到本目录的测试；
否则可能被 site-packages 里某些第三方包自带的 `tests` 目录抢先解析。

另外：与生产队列保持一致，把 BLAS/Numba 线程限制为 1。
默认线程数（本机 12）会让 numba 的 parfor lowering 在部分 Windows 环境病态变慢，
导致测试套件偶发数分钟不结束；生产路径（queue.drain）本来就用 1 线程。
"""
import os

for _key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ.setdefault(_key, '1')
