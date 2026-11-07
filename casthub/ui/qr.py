"""QR codes for QML: `image://qr/<url-encoded payload>` → black modules on transparent."""

from __future__ import annotations

from urllib.parse import unquote

import qrcode
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider


def matrix(payload: str) -> list[list[bool]]:
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    q.add_data(payload)
    q.make(fit=True)
    return q.get_matrix()


class QrProvider(QQuickImageProvider):
    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, id_: str, size: QSize, requested: QSize) -> QImage:  # noqa: N802 (Qt API)
        m = matrix(unquote(id_))
        n = len(m)
        side = max(requested.width(), requested.height(), n * 4)
        cell = max(1, side // n)
        img = QImage(n * cell, n * cell, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        for y, row in enumerate(m):
            for x, on in enumerate(row):
                if on:
                    p.fillRect(x * cell, y * cell, cell, cell, QColor("#0B0B0B"))
        p.end()
        if size is not None:
            size.setWidth(img.width())
            size.setHeight(img.height())
        return img
