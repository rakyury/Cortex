"""Explicit RAM-only diagnostic VM upload UI; all CAN operations belong to transport."""
from pathlib import Path
import struct

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QComboBox, QSpinBox,
    QPushButton, QCheckBox, QFileDialog, QMessageBox,
)
from ..hal_can import validate_program, READABLE_CHANNELS
from ..models.hal_telemetry import HAL_CHANNELS

OPERATIONS = (('Source value', None), ('Add', 0x10), ('Subtract', 0x11),
              ('Minimum', 0x50), ('Maximum', 0x51), ('Greater than', 0x34),
              ('Greater or equal', 0x35), ('Less than', 0x32),
              ('Less or equal', 0x33), ('Equal', 0x30), ('Not equal', 0x31))
BUSY_STATES = {'begin', 'data', 'commit', 'query', 'abort'}


class HalVmEditor(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.client = None
        self.loaded_program = None
        self.setMinimumWidth(290)
        layout = QVBoxLayout(self)
        hint = QLabel('Device diagnostic expression · RAM only until reset.\n'
                      'Result appears on channel 300. Outputs stay OFF.\n'
                      'Simulation must pass before physical bench testing.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.mode = QComboBox()
        self.mode.addItems(['Build expression', 'Loaded validated binary'])
        self.source = QComboBox()
        for number, label, unit in HAL_CHANNELS:
            if number in READABLE_CHANNELS:
                self.source.addItem(f'{label} · {unit}', number)
        self.operation = QComboBox()
        for label, opcode in OPERATIONS:
            self.operation.addItem(label, opcode)
        self.constant = QSpinBox()
        self.constant.setRange(-2147483648, 2147483647)
        form = QFormLayout()
        form.addRow('Program', self.mode)
        form.addRow('HAL source', self.source)
        form.addRow('Operation', self.operation)
        form.addRow('Integer constant', self.constant)
        layout.addLayout(form)
        self.file_label = QLabel('No binary loaded · maximum 128 bytes')
        self.file_label.setTextFormat(Qt.TextFormat.PlainText)
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)
        self.load_btn = QPushButton('Load validated .bin…')
        self.load_btn.clicked.connect(self.load_program)
        layout.addWidget(self.load_btn)
        self.arm = QCheckBox('Arm diagnostic VM upload')
        self.arm.toggled.connect(self._arm_changed)
        layout.addWidget(self.arm)
        buttons = QHBoxLayout()
        self.upload_btn = QPushButton('Confirm upload…')
        self.upload_btn.clicked.connect(self.upload_program)
        self.query_btn = QPushButton('Read active CRC')
        self.query_btn.clicked.connect(self.query_program)
        buttons.addWidget(self.upload_btn)
        buttons.addWidget(self.query_btn)
        layout.addLayout(buttons)
        self.cancel_btn = QPushButton('Cancel transaction')
        self.cancel_btn.clicked.connect(self.cancel_upload)
        layout.addWidget(self.cancel_btn)
        self.status = QLabel('Disconnected · no program uploaded')
        self.status.setWordWrap(True)
        self.status.setAccessibleName('HAL VM upload status')
        layout.addWidget(self.status)
        warning = QLabel('ACK plus matching CRC readback confirms the RAM program bytes, '
                         'not hardware validation. Integer add/subtract wrap as signed 32-bit. '
                         'A lost COMMIT reply is unknown; use readback, never automatic recommit.')
        warning.setWordWrap(True)
        layout.addWidget(warning)
        layout.addStretch()
        self.mode.currentIndexChanged.connect(self.refresh)
        self.operation.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def set_client(self, client):
        if client is not self.client:
            self.arm.blockSignals(True)
            self.arm.setChecked(False)
            self.arm.blockSignals(False)
            self.client = client
        self.refresh()

    def program(self):
        if self.mode.currentIndex() == 1:
            if self.loaded_program is None:
                raise ValueError('Load a validated binary first.')
            return validate_program(self.loaded_program)
        code = b'\x02' + struct.pack('<H', self.source.currentData())
        opcode = self.operation.currentData()
        if opcode is not None:
            code += b'\x01' + struct.pack('<i', self.constant.value()) + bytes([opcode])
        return validate_program(code + b'\xff')

    def refresh(self):
        client = self.client
        busy = client is not None and client.upload.state in BUSY_STATES
        allowed = client is not None and client.can_upload()
        if not allowed and client is not None:
            client.armed = False
        armed = bool(allowed and client.armed)
        self.arm.blockSignals(True)
        self.arm.setChecked(armed)
        self.arm.blockSignals(False)
        self.arm.setEnabled(bool(allowed and not busy))
        self.upload_btn.setEnabled(bool(armed and not busy))
        self.query_btn.setEnabled(bool(client is not None and client.online() and not busy))
        self.cancel_btn.setEnabled(bool(busy))
        builder = self.mode.currentIndex() == 0
        self.source.setEnabled(builder and not busy)
        self.operation.setEnabled(builder and not busy)
        self.constant.setEnabled(builder and self.operation.currentData() is not None and not busy)
        self.mode.setEnabled(not busy)
        self.load_btn.setEnabled(not busy)
        if client is None:
            self.status.setText('Disconnected · no upload confirmation for this connection')
        else:
            upload = client.upload
            crc = f' · active CRC {upload.active_crc:08X}' if upload.active_crc is not None else ''
            confirmation = 'ACK + CRC verified (RAM only)' if upload.verified else 'Not verified'
            error = f' · {upload.error}' if upload.error else ''
            readiness = '' if allowed else ' · fresh interlocked HAL upload capability required'
            self.status.setText(f'{upload.state} · {upload.offset}/{upload.length} bytes · '
                                f'{confirmation}{crc}{error}{readiness}')

    def _arm_changed(self, checked):
        if self.client is not None:
            self.client.armed = bool(checked and self.client.can_upload())
        self.refresh()

    def load_program(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Load diagnostic VM binary', '', 'VM binary (*.bin)')
        if not path:
            return
        # A replacement file always requires fresh authorization; rejected loads
        # must never leave a previous binary silently selected for upload.
        self.arm.setChecked(False)
        self.loaded_program = None
        self.mode.setCurrentIndex(1)
        try:
            with Path(path).open('rb') as source:
                code = source.read(129)
            code = validate_program(code)
            self.loaded_program = code
            self.mode.setCurrentIndex(1)
            self.file_label.setText(f'{Path(path).name} · {len(code)} validated bytes')
        except (ValueError, OSError) as error:
            self.file_label.setText(f'Binary rejected: {error}')
        self.refresh()

    def upload_program(self):
        client = self.client
        if client is None or not client.armed or not client.can_upload():
            self.refresh()
            return
        error_text = None
        try:
            code = self.program()
            reply = QMessageBox.question(self, 'Confirm diagnostic RAM upload',
                f'Upload {len(code)} bytes to the HAL diagnostic VM?\n'
                'Outputs remain OFF. The program lasts until reset; this is not a firmware flash.')
            if reply != QMessageBox.StandardButton.Yes:
                return
            # Transport rechecks freshness/interlock after the modal confirmation.
            client.begin_upload(code, confirmed=True)
        except Exception as error:
            error_text = f'Upload rejected: {error}'
        finally:
            client.armed = False
            self.refresh()
            if error_text:
                self.status.setText(error_text)

    def query_program(self):
        if self.client is None:
            return
        try:
            self.client.query_readback(expected_crc=self.client.upload.expected_crc)
            self.refresh()
        except Exception as error:
            self.refresh()
            self.status.setText(f'Readback failed: {error}')

    def cancel_upload(self):
        if self.client is None:
            return
        try:
            self.client.cancel()
            self.refresh()
        except Exception as error:
            self.refresh()
            self.status.setText(f'Cancel failed: {error}')
