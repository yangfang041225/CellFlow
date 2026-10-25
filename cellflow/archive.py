"""安全解压：拒绝路径穿越、绝对路径、盘符、UNC、链接与超标压缩包。

只允许把普通文件/目录写到 target 目录内部；任何违规都在写入前报错。
"""
from pathlib import Path
import re
import tarfile
import zipfile

MAX_FILES = 5000
MAX_BYTES = 4 * 1024 ** 3  # 解压后累计 4 GiB
MAX_DEPTH = 8


class ArchiveError(ValueError):
    pass


_DRIVE = re.compile(r'^[A-Za-z]:')


def _safe_member_path(name, target):
    raw = str(name).replace('\\', '/').strip()
    if not raw or raw in ('.', '..'):
        raise ArchiveError(f'非法成员名：{name!r}')
    if raw.startswith('/') or raw.startswith('//') or _DRIVE.match(raw):
        raise ArchiveError(f'拒绝绝对路径/盘符/UNC 成员：{name!r}')
    parts = [part for part in raw.split('/') if part not in ('', '.')]
    if any(part == '..' for part in parts):
        raise ArchiveError(f'拒绝父目录穿越成员：{name!r}')
    if len(parts) > MAX_DEPTH:
        raise ArchiveError(f'压缩包层级过深：{name!r}')
    final = (target / Path(*parts)).resolve()
    if not final.is_relative_to(target):
        raise ArchiveError(f'成员越界：{name!r}')
    return final


def _write_stream(source, destination, state, limits):
    max_files, max_bytes = limits
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, 'wb') as handle:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            state['bytes'] += len(chunk)
            if state['bytes'] > max_bytes:
                raise ArchiveError(f'解压后累计大小超过上限 {max_bytes // 1024 ** 3} GiB')
    return state


def safe_extract(archive_path, target, max_files=MAX_FILES, max_bytes=MAX_BYTES):
    archive_path = Path(archive_path)
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    state = {'bytes': 0, 'files': 0}
    limits = (max_files, max_bytes)

    def count_file():
        state['files'] += 1
        if state['files'] > max_files:
            raise ArchiveError(f'压缩包文件数超过上限 {max_files}')

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as zf:
            for info in zf.infolist():
                destination = _safe_member_path(info.filename, target)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ArchiveError(f'拒绝符号链接成员：{info.filename!r}')
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                count_file()
                with zf.open(info) as source:
                    _write_stream(source, destination, state, limits)
    elif tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path, 'r:*') as tf:
            for member in tf.getmembers():
                destination = _safe_member_path(member.name, target)
                if member.issym() or member.islnk():
                    raise ArchiveError(f'拒绝链接成员：{member.name!r}')
                if not (member.isfile() or member.isdir()):
                    raise ArchiveError(f'拒绝特殊文件成员：{member.name!r}')
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                count_file()
                source = tf.extractfile(member)
                if source is None:
                    raise ArchiveError(f'成员无法读取：{member.name!r}')
                with source:
                    _write_stream(source, destination, state, limits)
    else:
        raise ArchiveError('不是可识别的 zip / tar 压缩包')
    return target
