"""把报告 HTML 转成 PDF（Qt 官方绘制，本地、无浏览器），带页眉与页码。

用法（CLI）：
    python -m cellflow.pdf <报告.html> <输出.pdf> [页眉文字]

说明：
- 必须在默认 Windows 平台插件下运行（offscreen 插件没有中文字体，PDF 会空白）；
- 图片按 HTML 相对路径解析（设置 baseUrl）；
- 每页绘制页眉（任务/日期）与页码，页脚为固定来源说明；
- Qt 只支持 HTML 子集，报告模板本身是简单表格/标题，够用。
"""
from pathlib import Path
import sys


def html_to_pdf(html_path, pdf_path, header=None):
    html_path = Path(html_path)
    pdf_path = Path(pdf_path)
    if not html_path.is_file():
        raise RuntimeError(f'找不到 HTML：{html_path}')
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QMarginsF, QRectF, QSizeF, QUrl, Qt
    from PySide6.QtGui import QFont, QPageSize, QPainter, QTextDocument
    from PySide6.QtPrintSupport import QPrinter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    document = QTextDocument()
    document.setBaseUrl(QUrl.fromLocalFile(str(html_path.parent) + '/'))
    document.setHtml(html_path.read_text(encoding='utf-8'))

    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(pdf_path))
    printer.setPageSize(QPageSize(QPageSize.A4))
    printer.setPageMargins(QMarginsF(0, 0, 0, 0))
    printer.setDocName(html_path.stem)

    rect = printer.pageRect(QPrinter.Point)
    margin = 28
    header_height = 18
    footer_height = 16
    content_width = rect.width() - 2 * margin
    content_height = rect.height() - 2 * margin - header_height - footer_height
    document.setPageSize(QSizeF(content_width, content_height))

    title = header or f'CellFlow · {html_path.stem}'
    painter = QPainter(printer)
    painter.setFont(QFont('Microsoft YaHei', 7))
    pages = max(1, document.pageCount())
    for index in range(pages):
        if index:
            printer.newPage()
        painter.setPen(Qt.darkGray)
        painter.drawText(QRectF(margin, margin * 0.4, content_width, header_height),
                         Qt.AlignLeft | Qt.AlignVCenter, title)
        painter.drawText(QRectF(margin, margin * 0.4, content_width, header_height),
                         Qt.AlignRight | Qt.AlignVCenter, f'第 {index + 1} / {pages} 页')
        painter.save()
        painter.translate(margin, margin + header_height)
        painter.setClipRect(QRectF(0, 0, content_width, content_height))
        document.drawContents(painter, QRectF(0, index * content_height, content_width, content_height))
        painter.restore()
        painter.setPen(Qt.gray)
        painter.drawText(QRectF(margin, rect.height() - margin + 2, content_width, footer_height),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         '由 CellFlow 本地生成；结论以正文与证据边界为准')
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
