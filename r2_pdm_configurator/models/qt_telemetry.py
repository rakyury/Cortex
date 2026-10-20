"""Qt table adapter over immutable telemetry snapshots."""
from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PyQt6.QtGui import QColor
from .telemetry import ROWS, snapshot
from .computed import ComputedChannels
from .hal_telemetry import HAL_ROWS, hal_snapshot


class TelemetryModel(QAbstractTableModel):
    HEADERS = ('ID', 'Channel', 'Value', 'Unit', 'Status', 'Age')
    COLORS = {'stale': '#dbb96b', 'invalid': '#f07e7e', 'degraded': '#dbb96b',
              'unavailable': '#8994a5', 'fault': '#f07e7e'}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.client = None
        self.protocol = "legacy"
        self.computed = ComputedChannels()
        self.rows = list(ROWS)
        self.samples = snapshot(None)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.samples):
            return None
        row = self.samples[index.row()]
        if role == Qt.ItemDataRole.DisplayRole and 0 <= index.column() < len(self.HEADERS):
            return row.columns[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.detail
        if role == Qt.ItemDataRole.ForegroundRole and row.quality in self.COLORS:
            return QColor(self.COLORS[row.quality])
        return None

    def refresh(self):
        if self.protocol == 'hal':
            samples = hal_snapshot(self.client)
            rows = list(HAL_ROWS)
        else:
            base = snapshot(self.client)
            samples = base + self.computed.evaluate(base)
            rows = list(ROWS) + [(row.channel_id, row.label, 'computed', i)
                                 for i, row in enumerate(samples[len(ROWS):])]
        if rows != self.rows:
            self.beginResetModel()
            self.rows, self.samples = rows, samples
            self.endResetModel()
        else:
            self.samples = samples
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(self.HEADERS) - 1))
