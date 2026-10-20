"""R2 Synapse PDM CAN configurator - PyQt6 desktop front end.

A first, separate application. Existing Cortex (USB protocol) is unchanged.
For critical loads a CAN config write is *not* proof the PDM applied it.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QTableView, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

if __package__:
    from .pdm_can import PdmCanClient, open_adapter
    from .models.qt_telemetry import TelemetryModel
    from .models.telemetry import diagnostics
    from .ui.trend import TelemetryTrend
    from .ui.computed import ComputedChannelEditor
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from r2_pdm_configurator.pdm_can import PdmCanClient, open_adapter
    from r2_pdm_configurator.models.qt_telemetry import TelemetryModel
    from r2_pdm_configurator.models.telemetry import diagnostics
    from r2_pdm_configurator.ui.trend import TelemetryTrend
    from r2_pdm_configurator.ui.computed import ComputedChannelEditor


INTERFACES = (
    ("ECUMaster USBtoCAN (R2 Dash bridge)", "eusb2can", ""),
    ("PEAK PCAN-USB", "pcan", "PCAN_USBBUS1"),
    ("SLCAN / CANable", "slcan", "COM5"),
    ("SocketCAN", "socketcan", "can0"),
    ("Kvaser", "kvaser", "0"),
    ("Vector", "vector", "0"),
    ("Virtual (test)", "virtual", "r2-pdm"),
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("R2 | Synapse PDM — CAN Configurator")
        self.resize(1450, 850)
        self.client = None
        self.model = TelemetryModel(self)
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self._tick)
        self.last_refresh = 0.0
        self._make_ui()
        self.setStyleSheet("""
        QWidget { color: #d7deea; background: #171d26; font-size: 12px; }
        QGroupBox { border: 1px solid #334050; border-radius: 6px; margin-top: 13px; padding: 9px; }
        QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 1px 5px; color: #8dbdec; }
        QLineEdit,QComboBox,QSpinBox,QTreeWidget,QTableView {
            background: #222b37; border: 1px solid #394658; padding: 5px; }
        QTableView { alternate-background-color: #1d2632; }
        QPushButton { background: #234c70; border: 1px solid #466684; border-radius: 4px; padding: 7px 12px; }
        QPushButton:hover { background: #315f89; }
        QPushButton:disabled { color: #7f8792; background: #252d36; }
        QHeaderView::section { background: #273342; color: #d5ddea; border: none; padding: 6px; }
        """)

    def _make_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        bar = QGroupBox("Connection / CAN transport")
        form = QGridLayout(bar)
        self.adapter = QComboBox()
        for label, iface, default_channel in INTERFACES:
            self.adapter.addItem(label, (iface, default_channel))
        self.adapter.currentIndexChanged.connect(self._choose_adapter)
        self.port = QLineEdit("")
        self.bitrate = QComboBox()
        for b in (125000, 250000, 500000, 1000000):
            self.bitrate.addItem(f"{b // 1000} kbit/s", b)
        self.bitrate.setCurrentIndex(2)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connection)
        self.status = QLabel("Disconnected")
        self.status.setStyleSheet("color:#dbb96b")
        form.addWidget(QLabel("Interface"), 0, 0)
        form.addWidget(self.adapter, 0, 1)
        form.addWidget(QLabel("Channel"), 0, 2)
        form.addWidget(self.port, 0, 3)
        form.addWidget(QLabel("Bitrate"), 0, 4)
        form.addWidget(self.bitrate, 0, 5)
        form.addWidget(self.connect_btn, 0, 6)
        form.addWidget(self.status, 1, 0, 1, 7)
        outer.addWidget(bar)

        self.tree = QTreeWidget()
        self.tree.setMinimumWidth(215)
        self.tree.setHeaderLabels(["Channels / CAN"])
        for name, kind in (("SYSTEM", "system"), ("OUTPUTS", "output"),
                           ("DIGITAL INPUTS", "digital"), ("ANALOGUE INPUTS", "analogue")):
            item = QTreeWidgetItem([name])
            self.tree.addTopLevelItem(item)
            for row_index, (cid, label, row_kind, _) in enumerate(self.model.rows):
                if row_kind == kind:
                    child = QTreeWidgetItem([label])
                    child.setData(0, Qt.ItemDataRole.UserRole, row_index)
                    item.addChild(child)
        self.tree.expandAll()
        self.computed_tree = QTreeWidgetItem(["COMPUTED · LOCAL"])
        self.tree.addTopLevelItem(self.computed_tree)
        self.tree.itemClicked.connect(self._select_tree_channel)
        self.channel_dock = QDockWidget("Channels", self)
        self.channel_dock.setObjectName("channelsDock")
        self.channel_dock.setWidget(self.tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.channel_dock)
        center = QWidget()
        central_layout = QVBoxLayout(center)
        central_layout.addWidget(QLabel("LIVE TELEMETRY  |  14 Outputs  /  8 Digital  /  8 Analogue"))
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 135), (2, 75), (3, 70), (4, 200), (5, 65)):
            self.table.setColumnWidth(column, width)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        central_layout.addWidget(self.table)
        self.table.selectionModel().currentRowChanged.connect(self._selection_changed)
        self.trend = TelemetryTrend()
        central_layout.addWidget(self.trend)
        outer.addWidget(center, 1)
        self.diagnostic_text = QLabel("Disconnected — no live telemetry")
        self.diagnostic_text.setWordWrap(True)
        self.diagnostic_text.setMargin(10)
        self.diagnostic_text.setAccessibleName("Telemetry diagnostics")
        self.diagnostic_dock = QDockWidget("Diagnostics", self)
        self.diagnostic_dock.setObjectName("diagnosticsDock")
        self.diagnostic_dock.setWidget(self.diagnostic_text)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.diagnostic_dock)
        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.channel_dock.toggleViewAction())
        view_menu.addAction(self.diagnostic_dock.toggleViewAction())
        self.computed_editor = ComputedChannelEditor(self.model.computed)
        self.computed_editor.changed.connect(self._computed_changed)
        self.computed_dock = QDockWidget("Computed channels · local preview", self)
        self.computed_dock.setObjectName("computedChannelsDock")
        self.computed_dock.setWidget(self.computed_editor)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.computed_dock)
        view_menu.addAction(self.computed_dock.toggleViewAction())

        panel = QGroupBox("Configuration over CAN  (live controller required)")
        row = QHBoxLayout(panel)
        self.channel = QSpinBox()
        self.channel.setRange(1, 14)
        self.high = QSpinBox()
        self.high.setRange(0, 17)
        self.high.setValue(10)
        self.low = QSpinBox()
        self.low.setRange(0, 17)
        self.retries = QSpinBox()
        self.retries.setRange(0, 255)
        self.retries.setValue(3)
        self.arm = QCheckBox("Arm CAN writes")
        self.arm.toggled.connect(self._arm_changed)
        self.send = QPushButton("Send channel configuration")
        self.send.clicked.connect(self._write_config)
        self.send.setEnabled(False)
        row.addWidget(QLabel("Output"))
        row.addWidget(self.channel)
        row.addWidget(QLabel("High A"))
        row.addWidget(self.high)
        row.addWidget(QLabel("Low A"))
        row.addWidget(self.low)
        row.addWidget(QLabel("Retries"))
        row.addWidget(self.retries)
        row.addWidget(self.arm)
        row.addWidget(self.send)
        outer.addWidget(panel)
        help_text = QLabel(
            "Firmware uses integer-A limits for CAN F1 writes. No config-write ACK exists; "
            "always confirm by re-reading. CAN output control is disabled in this UI.")
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color:#9eabb9")
        outer.addWidget(help_text)
        self.operation_status = QLabel("No configuration write requested")
        self.operation_status.setWordWrap(True)
        outer.addWidget(self.operation_status)
        self._choose_adapter()

    def _select_tree_channel(self, item, column):
        row = item.data(0, Qt.ItemDataRole.UserRole)
        if row is not None:
            self.table.selectRow(row)

    def _selection_changed(self, current, previous):
        if current.isValid():
            self.trend.append(self.model.samples[current.row()])

    def _computed_changed(self):
        self.trend.clear()  # A changed formula starts a new history.
        self.table.clearSelection()
        self._refresh_telemetry()
        self.computed_tree.takeChildren()
        for row_index, (_, label, kind, _) in enumerate(self.model.rows):
            if kind == 'computed':
                child = QTreeWidgetItem([label])
                child.setData(0, Qt.ItemDataRole.UserRole, row_index)
                self.computed_tree.addChild(child)
        self.computed_tree.setExpanded(True)

    def _refresh_telemetry(self):
        self.model.refresh()
        self.diagnostic_text.setText(diagnostics(self.client, self.model.samples))
        row = self.table.currentIndex().row()
        if row >= 0:
            self.trend.append(self.model.samples[row])
        self._sync_write_controls()
        readback = getattr(self.client, 'last_config_readback', None)
        if readback is not None:
            detail = ', '.join(f'{name}: expected {values[0]}, observed {values[1]}'
                               for name, values in readback.discrepancies.items())
            self.operation_status.setText(f'OUT {readback.number}: {readback.status}'
                                          + (f' — {detail}' if detail else ''))

    def _sync_write_controls(self):
        online = self.client is not None and self.client.online()
        armed = bool(online and self.client.armed)
        self.arm.blockSignals(True)
        self.arm.setChecked(armed)
        self.arm.blockSignals(False)
        self.send.setEnabled(armed)

    def _choose_adapter(self):
        _, channel = self.adapter.currentData()
        self.port.setText(channel)

    def _toggle_connection(self):
        if self.client:
            self._disconnect()
            return
        try:
            iface, _ = self.adapter.currentData()
            bus = open_adapter(iface, self.port.text(), self.bitrate.currentData())
            self.client = PdmCanClient(bus)
            self.operation_status.setText("No configuration write requested for this connection")
            self.model.client = self.client
            self._refresh_telemetry()
            self.timer.start()
            self.connect_btn.setText("Disconnect")
            self.status.setText("Connected to adapter; waiting for PDM 0x720 heartbeat…")
            self.adapter.setEnabled(False)
            self.port.setEnabled(False)
            self.bitrate.setEnabled(False)
        except Exception as exc:
            QMessageBox.critical(self, "CAN connection failed", str(exc))

    def _disconnect(self):
        self.timer.stop()
        self.arm.setChecked(False)
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None
        self.model.client = None
        self._refresh_telemetry()
        self.trend.clear()
        self.adapter.setEnabled(True)
        self.port.setEnabled(True)
        self.bitrate.setEnabled(True)
        self.connect_btn.setText("Connect")
        self.status.setText("Disconnected")
        self.operation_status.setText("Disconnected — previous configuration writes remain unconfirmed")

    def _arm_changed(self, checked):
        if not self.client:
            self.arm.blockSignals(True)
            self.arm.setChecked(False)
            self.arm.blockSignals(False)
            return
        if checked and not self.client.online():
            self.arm.setChecked(False)
            QMessageBox.warning(self, "Not connected", "No fresh valid PDM heartbeat.")
            return
        self.client.armed = bool(checked)
        self._sync_write_controls()

    def _write_config(self):
        if not self.client or not self.client.armed or not self.client.online():
            QMessageBox.warning(self, "Writes disarmed", "Connect to a live PDM and explicitly arm CAN writes.")
            return
        if self.low.value() > self.high.value():
            QMessageBox.warning(self, "Invalid thresholds", "Low-current threshold exceeds high-current threshold.")
            return
        number = self.channel.value()
        response = QMessageBox.question(
            self, "Confirm live CAN configuration",
            f"Change OUTPUT {number} protection? High {self.high.value()} A, "
            f"low {self.low.value()} A, retries {self.retries.value()}.\n"
            "There is no write acknowledgement. Do not perform this with critical loads powered.")
        if response != QMessageBox.StandardButton.Yes:
            return
        try:
            self.client.configure_channel(number, high_current=self.high.value(),
                                          low_current=self.low.value(), retries=self.retries.value())
            self.operation_status.setText(f"Config sent to OUT {number}; readback required (NO ACK)")
        except Exception as exc:
            QMessageBox.critical(self, "CAN write failed", str(exc))
        finally:
            self.arm.setChecked(False)

    def _tick(self):
        if not self.client:
            return
        try:
            self.client.tick()
            current = time.monotonic()
            if current - self.last_refresh > 0.2:
                self.last_refresh = current
                self._refresh_telemetry()
                self.status.setText(
                    ("PDM ONLINE" if self.client.online() else "Waiting / PDM offline")
                    + f"   · RX {self.client.rx_frames}   · bad/timeouts {self.client.bad_frames}"
                    + f"   · outputs {len(self.client.channels)}/14"
                )
        except Exception as exc:
            self._disconnect()
            QMessageBox.critical(self, "CAN bus error", str(exc))

    def closeEvent(self, event):
        self._disconnect()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
