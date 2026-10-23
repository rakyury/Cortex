"""Bounded live trend with explicit gaps when telemetry becomes unusable."""
from collections import deque
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget


class TelemetryTrend(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.history = deque(maxlen=180)
        self.channel_id = None
        self.caption = 'Select a telemetry channel'
        self.setMinimumHeight(135)
        self.setAccessibleName('Selected telemetry trend')

    def append(self, row):
        if row.channel_id != self.channel_id:
            self.history.clear()
            self.channel_id = row.channel_id
        self.caption = f'{row.label} · {row.unit} · {row.status}'
        self.history.append(row.numeric)
        self.update()

    def clear(self):
        self.history.clear()
        self.channel_id = None
        self.caption = 'Disconnected — no live trend'
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#171d26'))
        painter.setPen(QColor('#d7deea'))
        painter.drawText(10, 20, self.caption)
        values = [value for value in self.history if value is not None]
        if not values:
            painter.drawText(10, 55, 'Waiting for fresh, valid samples')
            return
        low, high = min(values), max(values)
        painter.drawText(10, 40, f'{low:g} … {high:g}')
        span = max(high - low, 1.0)
        width, height = max(1, self.width() - 20), max(1, self.height() - 60)
        painter.setPen(QPen(QColor('#8dbdec'), 2))
        previous = None
        for index, value in enumerate(self.history):
            if value is None:
                previous = None
                continue
            point = QPointF(10 + width * index / max(1, len(self.history) - 1),
                            self.height() - 10 - height * (value - low) / span)
            if previous is not None:
                painter.drawLine(previous, point)
            previous = point
