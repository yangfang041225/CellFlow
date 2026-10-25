"""One native Qt window around the existing local React application."""
from pathlib import Path
import ctypes
import json
import os
import sys


def initial_window_size(available_width, available_height, desired=(1180, 780),
                       minimum=(820, 560), margin=(80, 100)):
    """按屏幕可用区域收敛窗口尺寸；返回 (width, height, min_width, min_height)。"""
    avail_w = max(320, int(available_width) - margin[0])
    avail_h = max(240, int(available_height) - margin[1])
    width = min(desired[0], avail_w)
    height = min(desired[1], avail_h)
    return width, height, min(width, 980), min(height, 620)


def run_window(root, logs, url, instance, log):
    # Local to this application; no changes to Windows proxy or GPU settings.
    _flags = '--no-proxy-server --disable-gpu'
    _extra = os.environ.get('CELLFLOW_WEBVIEW_FLAGS', '').strip()
    if _extra:
        _flags += ' ' + _extra
    os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = _flags
    # Capture native Chromium/Qt messages too, including when launched by pythonw.
    native_log = (logs / 'qt-native.log').open('a', encoding='utf-8', buffering=1)
    for fd in (1, 2):
        os.dup2(native_log.fileno(), fd)
    sys.stdout = sys.stderr = native_log

    from PySide6.QtCore import QEvent, QTimer, QUrl, Qt, qInstallMessageHandler
    from PySide6.QtGui import QIcon, QKeySequence, QShortcut
    from PySide6.QtNetwork import QNetworkProxy
    from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView

    qInstallMessageHandler(lambda kind, context, message: log(f'QT {kind}: {message}'))
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication([sys.argv[0]])
    app.setApplicationName('CellFlow')
    app.setWindowIcon(QIcon(str(root / 'cellflow.ico')))
    QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))

    from urllib.parse import urlsplit
    local_origin = urlsplit(url)
    if local_origin.scheme != 'http' or local_origin.hostname != '127.0.0.1':
        raise ValueError('Desktop origin must be a loopback HTTP URL')

    class LocalPage(QWebEnginePage):
        def acceptNavigationRequest(self, target, kind, main_frame):
            allowed = target.scheme() == 'http' and target.host() == '127.0.0.1' and target.port() == local_origin.port
            if not allowed and main_frame:
                log('Navigation rejected outside local API: ' + target.toString())
            return allowed

        def javaScriptConsoleMessage(self, level, message, line, source):
            log(f'JS {level} {source}:{line}: {message}')

    class Window(QMainWindow):
        def changeEvent(self, event):
            super().changeEvent(event)
            if event.type() == QEvent.Type.WindowStateChange:
                log('WINDOW_STATE ' + str(self.windowState()))

    window = Window()
    window.setWindowTitle('CellFlow')
    # 按当前屏幕可用区域收敛初始窗口，避免小屏/任务栏遮挡；最小尺寸不高于可用区域
    screen = QApplication.primaryScreen()
    available = screen.availableGeometry() if screen is not None else None
    width, height = 1180, 780
    min_width, min_height = 980, 620
    if available is not None:
        width, height, min_width, min_height = initial_window_size(
            available.width(), available.height())
    window.resize(width, height)
    window.setMinimumSize(min_width, min_height)
    if available is not None:
        window.move(available.x() + max(0, (available.width() - width) // 2),
                    available.y() + max(0, (available.height() - height) // 2))
    view = QWebEngineView(window)
    profile = QWebEngineProfile('CellFlow', window)
    profile.setPersistentStoragePath(str(logs / 'qt-profile'))
    profile.setCachePath(str(logs / 'qt-profile/cache'))
    # 本地单用户应用：HTML/JS/CSS 更新必须立即生效，禁用 HTTP 缓存
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    page = LocalPage(profile, view)
    view.setPage(page)
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    window.setCentralWidget(view)

    # target=_blank stays in this native window; Alt+Left returns to the app.
    page.newWindowRequested.connect(lambda request: request.openIn(page))
    back = QShortcut(QKeySequence('Alt+Left'), window)
    back.activated.connect(view.back)
    reload_shortcut = QShortcut(QKeySequence('Ctrl+R'), window)
    reload_shortcut.activated.connect(view.reload)

    def download(item):
        path, _ = QFileDialog.getSaveFileName(window, '保存 CellFlow 文件', item.downloadFileName())
        if path:
            target = Path(path)
            item.setDownloadDirectory(str(target.parent))
            item.setDownloadFileName(target.name)
            item.accept()
        else:
            item.cancel()
    profile.downloadRequested.connect(download)

    ready = False
    closed = False
    attempts = 0
    probe = QTimer(window)
    probe.setInterval(250)

    def record(value):
        nonlocal ready
        if closed or ready or not isinstance(value, str):
            return
        try:
            data = json.loads(value)
        except ValueError:
            return
        if not all(name in data.get('views', []) for name in ('概览', '数据', '模型分析', '分析结果', '报告')):
            return
        data.update(pid=os.getpid(), renderer='QtWebEngine', width=window.width(), height=window.height(),
                    console_hwnd=int(ctypes.windll.kernel32.GetConsoleWindow()) if os.name == 'nt' else None)
        (logs / 'desktop-ready.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        instance.publish(url=url, title='CellFlow', state='ready', renderer='QtWebEngine')
        log('REACT_READY ' + json.dumps(data, ensure_ascii=False))
        ready = True
        probe.stop()

    def inspect():
        nonlocal attempts
        attempts += 1
        if attempts > 80:
            probe.stop()
            QMessageBox.critical(window, 'CellFlow', 'React 页面未能就绪，详情见 logs/app.log。')
            return
        page.runJavaScript("JSON.stringify({url:location.href,title:document.title,views:[...document.querySelectorAll('[role=menuitem]')].map(e=>e.textContent.trim()),background:getComputedStyle(document.body).backgroundColor})", record)
    probe.timeout.connect(inspect)

    def loaded(ok):
        log(f'QT_LOAD_FINISHED success={ok} url={view.url().toString()}')
        if not ok:
            QMessageBox.critical(window, 'CellFlow', '本机页面加载失败，详情见 logs/app.log。')
        elif not ready:
            probe.start()
    view.loadFinished.connect(loaded)

    def crashed(status, code):
        log(f'QT_RENDER_PROCESS_TERMINATED status={status} code={code}')
        QMessageBox.critical(window, 'CellFlow', '窗口渲染进程异常退出，请重新启动；详情见 logs/app.log。')
    page.renderProcessTerminated.connect(crashed)

    def closing():
        nonlocal closed
        closed = True
        probe.stop()
    app.aboutToQuit.connect(closing)
    instance.publish(url=url, title='CellFlow', state='shown', renderer='QtWebEngine')
    view.load(QUrl(url))
    window.show()
    log('WINDOW_SHOWN renderer=QtWebEngine size=1180x780 url=' + url)
    code = app.exec()
    log('WINDOW_CLOSED renderer=QtWebEngine')
    return code
