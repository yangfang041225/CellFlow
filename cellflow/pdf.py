"""把报告 HTML 转成 PDF（Qt 官方绘制，本地、无浏览器），带页眉与页码。

用法（CLI）：
    python -m cellflow.pdf <报告.html> <输出.pdf> [页眉文字]

单位约定（修复 F17：HighResolution 打印设备与 Point 坐标混用，正文被缩到约 0.72 pt）：
- 打印机使用 HighResolution（本机 1200 dpi），页面几何**全部**按 DevicePixel 计算；
- QTextDocument 按逻辑像素排版（Qt 的默认逻辑分辨率 96 dpi），所以字号先用
  `_px(10.5) = 14` 换算成逻辑像素（px），页面尺寸也用逻辑像素；
- 绘制正文时把 painter 缩放 `dpi / 96`，于是文档里的 1 逻辑像素 = 1/96 英寸，
  正文 10.5 pt 就真的落在 10.5 pt；不再出现“点数被当成设备像素”的缩小；
- 页眉、页码、页脚在设备像素坐标系里绘制，字体用物理 pt，保证 7–9 pt 可读。

说明：
- 必须在默认 Windows 平台插件下运行（offscreen 插件没有中文字体，PDF 会空白）；
- 图片按 HTML 相对路径解析（设置 baseUrl）；
- Qt 只支持 HTML 子集：先剥掉 `@media` 块（Qt 不认），再进入排版；
- 生成后校验文件存在且大于 1 KB，否则报错，不假装成功。
"""
from pathlib import Path
import math
import re
import sys

LOGICAL_DPI = 96.0          # QTextDocument 的排版分辨率
BODY_PT = 10.5              # 正文物理字号（验收要求 7–10 pt，取 10.5 再按 CSS 微调）
HEADER_PT = 8.0
FOOTER_PT = 7.5
FONT_FAMILY = 'Microsoft YaHei'
MARGIN_MM = (18.0, 16.0, 18.0, 16.0)


def _px(points):
    """物理 pt → QTextDocument 逻辑像素（96 dpi）。"""
    return float(points) * LOGICAL_DPI / 72.0


def _strip_at_media(markup):
    """剥掉 @media 块（Qt 不支持，且嵌套花括号会干扰它的 CSS 解析）。"""
    pattern = re.compile(r'@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}', re.S)
    return pattern.sub('', markup)


def _pt_to_px_in_styles(markup):
    """把 <style> 里的 pt 字号换成 96 dpi 的逻辑像素，保证排版与设备无关。"""

    def convert_style(match):
        body = match.group(1)

        def convert(match_pt):
            value = float(match_pt.group(1))
            return f'{round(value * LOGICAL_DPI / 72.0, 2)}px'

        return '<style>' + re.sub(r'(\d+(?:\.\d+)?)pt', convert, body) + '</style>'

    return re.sub(r'<style>(.*?)</style>', convert_style, markup, flags=re.S)


def _pdf_ready_html(text):
    markup = _strip_at_media(text)
    markup = _pt_to_px_in_styles(markup)
    return markup


def html_to_pdf(html_path, pdf_path, header=None):
    html_path = Path(html_path)
    pdf_path = Path(pdf_path)
    if not html_path.is_file():
        raise RuntimeError(f'找不到 HTML：{html_path}')
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QMarginsF, QRectF, QSizeF, QUrl, Qt
    from PySide6.QtGui import QColor, QFont, QPageLayout, QPageSize, QPainter, QTextDocument
    from PySide6.QtPrintSupport import QPrinter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    document = QTextDocument()
    document.setBaseUrl(QUrl.fromLocalFile(str(html_path.parent) + '/'))
    body_font = QFont(FONT_FAMILY)
    body_font.setPixelSize(round(_px(BODY_PT)))
    document.setDefaultFont(body_font)
    document.setHtml(_pdf_ready_html(html_path.read_text(encoding='utf-8')))

    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(pdf_path))
    printer.setPageSize(QPageSize(QPageSize.A4))
    printer.setPageMargins(QMarginsF(*MARGIN_MM), QPageLayout.Millimeter)
    printer.setDocName(html_path.stem)

    dpi = float(printer.resolution() or 1200)
    scale = dpi / LOGICAL_DPI
    rect = printer.pageRect(QPrinter.DevicePixel)      # 可打印区域，单位=设备像素
    header_dots = HEADER_PT / 72.0 * dpi
    footer_dots = FOOTER_PT / 72.0 * dpi
    content_w_dots = float(rect.width())
    content_h_dots = float(rect.height()) - header_dots - footer_dots
    if content_w_dots <= 0 or content_h_dots <= 0:
        raise RuntimeError('打印区域异常，无法排版')
    content_w = content_w_dots / scale                 # 逻辑像素
    content_h = content_h_dots / scale
    document.setPageSize(QSizeF(content_w, content_h))

    title = header or f'CellFlow · {html_path.stem}'
    try:
        pages = max(1, int(document.pageCount()))
    except Exception:
        pages = 1
    painter = QPainter(printer)
    try:
        for index in range(pages):
            if index:
                printer.newPage()
            # ---- 页眉（设备像素坐标 + 物理 pt 字体）
            painter.save()
            painter.setFont(QFont(FONT_FAMILY, HEADER_PT))
            painter.setPen(QColor('#5b7186'))
            painter.drawText(QRectF(0, 0, content_w_dots, header_dots),
                             Qt.AlignLeft | Qt.AlignVCenter, title)
            painter.drawText(QRectF(0, 0, content_w_dots, header_dots),
                             Qt.AlignRight | Qt.AlignVCenter,
                             f'第 {index + 1} / {pages} 页')
            painter.restore()
            # ---- 正文（逻辑像素坐标系；painter 缩放 dpi/96，并按页裁剪+平移）
            # 分段矩形既能裁剪本页内容，也让 Qt 的文本提取只包含本页文字；
            # 再手动平移 -index*页高，把该段放到页面顶部（否则内容会落到页外）。
            painter.save()
            painter.translate(0, header_dots)
            painter.scale(scale, scale)
            painter.translate(0, -index * content_h)
            document.drawContents(painter,
                                  QRectF(0, index * content_h, content_w, content_h))
            painter.restore()
            # ---- 页脚（设备像素坐标）
            painter.save()
            painter.setFont(QFont(FONT_FAMILY, FOOTER_PT))
            painter.setPen(QColor('#8899aa'))
            painter.drawText(QRectF(0, content_h_dots + header_dots + 2, content_w_dots,
                                    footer_dots),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             '由 CellFlow 本地生成；结论以正文与证据边界为准')
            painter.restore()
    finally:
        painter.end()

    if not pdf_path.is_file() or pdf_path.stat().st_size < 1024:
        raise RuntimeError('PDF 生成失败（文件为空或过小）')
    return pdf_path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) not in (2, 3):
        print('用法：python -m cellflow.pdf <报告.html> <输出.pdf> [页眉文字]', file=sys.stderr)
        return 2
    try:
        path = html_to_pdf(argv[0], argv[1], argv[2] if len(argv) == 3 else None)
    except Exception as exc:
        print(f'PDF 生成失败：{exc}', file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
