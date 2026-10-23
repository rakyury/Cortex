"""Passive HAL scheduler diagnostics. Deliberately has no command controls."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView
from ..models.start_diagnostics import start_diagnostics_view


class StartDiagnosticsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        notice = QLabel('READ ONLY · HAL CAN scheduler diagnostics. No start, cancel or output commands.\n'
            'Queue states and current reservations are diagnostic data, not proof of physical output state. '
            'Hardware actuation remains blocked pending HIL.')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setAccessibleName('HAL scheduler snapshot status')
        layout.addWidget(self.status)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setAccessibleName('HAL scheduler profile and current budget')
        layout.addWidget(self.summary)
        self.groups = QLabel()
        self.groups.setWordWrap(True)
        self.groups.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.groups)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['Channel', 'Queue', 'Virtual state', 'Delay / refusal',
                                               'Reserved', 'Group', 'Start slot'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setAccessibleName('Read-only HAL scheduler channels')
        self.table.setMinimumHeight(160)
        layout.addWidget(self.table)
        self.refresh(None)

    def refresh(self, client):
        view = start_diagnostics_view(client)
        self.status.setText(view.status)
        self.status.setStyleSheet('color:' + ('#75cba9' if view.quality == 'live' else '#e7a649'))
        self.summary.setText(view.summary)
        self.groups.setText(view.groups)
        selected = self.table.currentRow()
        self.table.setRowCount(len(view.rows))
        for row, values in enumerate(view.rows):
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        if view.rows and selected >= 0:
            self.table.selectRow(min(selected, len(view.rows) - 1))
