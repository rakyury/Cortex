"""R2 Synapse PDM CAN configurator - PyQt6 desktop front end.

A first, separate application. Existing Cortex (USB protocol) is unchanged.
For critical loads a CAN config write is *not* proof the PDM applied it.
"""
from __future__ import annotations

import sys
import time

from PyQt6.QtCore import Qt, QTimer, QAbstractTableModel, QModelIndex
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QSplitter, QTableView, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from pdm_can import PdmCanClient, open_adapter


INTERFACES = (
    ("ECUMaster USBtoCAN (R2 Dash bridge)", "eusb2can", ""),
    ("PEAK PCAN-USB", "pcan", "PCAN_USBBUS1"),
    ("SLCAN / CANable", "slcan", "COM5"),
    ("SocketCAN", "socketcan", "can0"),
    ("Kvaser", "kvaser", "0"),
    ("Vector", "vector", "0"),
    ("Virtual (test)", "virtual", "r2-pdm"),
)


class TelemetryModel(QAbstractTableModel):
    HEADERS = ("ID", "Channel", "Value", "Unit", "Status", "Age")
    def __init__(self, parent=None):
        super().__init__(parent)
        self.client: PdmCanClient | None = None
        self.rows = ([(f"SYS:{x}", x, "system", 0) for x in (
            "Voltage", "Current", "Temperature", "Errors", "Alive")] +
            [(f"OUT:{n:02}", f"Output {n:02}", "output", n) for n in range(1, 15)] +
            [(f"DI:{n:02}", f"Digital {n}", "digital", n) for n in range(1, 9)] +
            [(f"AI:{n:02}", f"Analogue {n}", "analogue", n) for n in range(1, 9)])
    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)
    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return len(self.HEADERS)
    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        cid, label, kind, num = self.rows[index.row()]
        if role == Qt.ItemDataRole.ForegroundRole:
            if self.client and not self.client.online():
                return QColor("#8994a5")
            return None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        value, unit, status, age = "—", "", "", "—"
        c = self.client
        if c is not None:
            now = c.now()
            if kind == "system":
                s = c.system
                if s.last_seen:
                    key = cid.split(":")[1]
                    vals = {
                        "Voltage": (f"{s.battery_v:.1f}", "V"),
                        "Current": (f"{s.total_current_a:.1f}", "A"),
                        "Temperature": (str(s.temperature_c), "°C"),
                        "Errors": (f"0x{s.error_flags:04X}", ""),
                        "Alive": (str(s.alive), ""),
                    }
                    value, unit = vals[key]
                    status = "Online" if c.online() else "Stale"
                    age = f"{now - s.last_seen:.1f} s"
            elif kind == "output" and num in c.channels:
                ch = c.channels[num]
                label = ch.name or label
                value, unit = f"{ch.current_a:.1f}", "A"
                status = "ON" if ch.enabled else "OFF"
                age = f"{now - ch.last_seen:.1f} s"
            elif kind == "digital" and c.digital_mask is not None:
                value = str((c.digital_mask >> (num - 1)) & 1)
                status = "Input"
            elif kind == "analogue" and num in c.analogues:
                ai = c.analogues[num]
                value, unit, status = f"{ai.value:.3f}", f"unit {ai.units}", f"{ai.voltage_v:.3f} V"
                age = f"{now - ai.last_seen:.1f} s"
        cols = (cid, label, value, unit, status, age)
        return cols[index.column()]
    def refresh(self):
        self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(self.HEADERS) - 1))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("R2 | Synapse PDM — CAN Configurator")
        self.resize(1250, 800)
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

        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Channels / CAN"])
        for name, prefix, count in (("SYSTEM", "SYS", 5), ("OUTPUTS", "OUT", 14),
                                    ("DIGITAL INPUTS", "DI", 8), ("ANALOGUE INPUTS", "AI", 8)):
            item = QTreeWidgetItem([name])
            self.tree.addTopLevelItem(item)
            for n in range(1, count + 1):
                item.addChild(QTreeWidgetItem([f"{prefix} {n:02}"]))
        self.tree.expandAll()
        split.addWidget(self.tree)
        center = QWidget()
        central_layout = QVBoxLayout(center)
        central_layout.addWidget(QLabel("LIVE TELEMETRY  |  14 Outputs  /  8 Digital  /  8 Analogue"))
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        central_layout.addWidget(self.table)
        split.addWidget(center)
        split.setSizes([270, 900])
        outer.addWidget(split, 1)

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
        self._choose_adapter()

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
            self.model.client = self.client
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
        self.model.refresh()
        self.adapter.setEnabled(True)
        self.port.setEnabled(True)
        self.bitrate.setEnabled(True)
        self.connect_btn.setText("Connect")
        self.status.setText("Disconnected")

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
            self.status.setText(f"Config sent to OUT {number}; readback required (NO ACK)")
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
                self.model.refresh()
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
