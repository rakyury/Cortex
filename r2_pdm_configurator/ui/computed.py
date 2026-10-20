"""Local computed-channel editor. Receives a model, never a CAN client."""
from pathlib import Path

from PyQt6.QtCore import QIODevice, QSaveFile, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QPushButton, QListWidget, QFileDialog,
)

from ..models.computed import SOURCES, OPERATIONS, Definition


class ComputedChannelEditor(QWidget):
    changed = pyqtSignal()

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.setMinimumWidth(290)
        layout = QVBoxLayout(self)
        hint = QLabel("Local preview · fresh telemetry only\n"
                      "Voltage: mV · current: mA · comparisons: 0 or 1.\n"
                      "These channels cannot control outputs.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.channel_list = QListWidget()
        self.channel_list.setAccessibleName("Computed channels")
        self.channel_list.setMaximumHeight(110)
        self.channel_list.currentRowChanged.connect(self._select)
        layout.addWidget(self.channel_list)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(48)
        self.name.setPlaceholderText("e.g. Low battery")
        self.source = QComboBox()
        self.other_source = QComboBox()
        self.other_source.addItem("Constant in source units", None)
        for source in SOURCES:
            self.source.addItem(source.label, source.channel_id)
            self.other_source.addItem(source.label, source.channel_id)
        self.operation = QComboBox()
        for key, label in OPERATIONS:
            self.operation.addItem(label, key)
        self.constant = QSpinBox()
        self.constant.setRange(-2147483648, 2147483647)
        self.constant.setAccessibleName("Comparison or arithmetic constant")
        form.addRow("Name", self.name)
        form.addRow("Source", self.source)
        form.addRow("Operation", self.operation)
        form.addRow("Second value", self.other_source)
        form.addRow("Constant", self.constant)
        layout.addLayout(form)
        self.operation.currentIndexChanged.connect(self._controls)
        self.other_source.currentIndexChanged.connect(self._controls)
        buttons = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.update_btn = QPushButton("Update")
        self.remove_btn = QPushButton("Remove")
        for button, action in ((self.add_btn, self.add_definition),
                               (self.update_btn, self.update_definition),
                               (self.remove_btn, self.remove_definition)):
            button.clicked.connect(action)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        files = QHBoxLayout()
        self.load_btn = QPushButton("Load local…")
        self.save_btn = QPushButton("Save local…")
        self.load_btn.clicked.connect(self.load_definitions)
        self.save_btn.clicked.connect(self.save_definitions)
        files.addWidget(self.load_btn)
        files.addWidget(self.save_btn)
        layout.addLayout(files)
        self.message = QLabel("Example: Voltage < 11500 gives a low-battery indicator.")
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.setAccessibleName("Computed channel editor status")
        layout.addWidget(self.message)
        layout.addStretch()
        self._controls()

    def _controls(self):
        binary = self.operation.currentData() != "identity"
        self.other_source.setEnabled(binary)
        self.constant.setEnabled(binary and self.other_source.currentData() is None)
        selected = self.channel_list.currentRow() >= 0
        self.update_btn.setEnabled(selected)
        self.remove_btn.setEnabled(selected)
        self.add_btn.setEnabled(len(self.model.definitions) < 16)

    def _definition(self):
        operation = self.operation.currentData()
        return Definition(self.name.text(), self.source.currentData(), operation,
                          self.other_source.currentData() if operation != "identity" else None,
                          self.constant.value() if operation != "identity" else 0)

    def _select(self, index):
        if 0 <= index < len(self.model.definitions):
            definition = self.model.definitions[index]
            self.name.setText(definition.name)
            self.source.setCurrentIndex(self.source.findData(definition.source_id))
            self.operation.setCurrentIndex(self.operation.findData(definition.operation))
            self.other_source.setCurrentIndex(self.other_source.findData(definition.other_source_id))
            self.constant.setValue(definition.constant)
        self._controls()

    def _updated(self, selected=-1):
        self.channel_list.blockSignals(True)
        self.channel_list.clear()
        self.channel_list.addItems([d.name for d in self.model.definitions])
        self.channel_list.blockSignals(False)
        self.channel_list.setCurrentRow(selected)
        self._controls()
        self.message.setText("Local definitions updated. No device write was sent.")
        self.changed.emit()

    def add_definition(self):
        try:
            self.model.add(self._definition())
            self._updated(len(self.model.definitions) - 1)
        except ValueError as error:
            self.message.setText(str(error))

    def update_definition(self):
        index = self.channel_list.currentRow()
        if index < 0:
            return
        try:
            self.model.replace(index, self._definition())
            self._updated(index)
        except ValueError as error:
            self.message.setText(str(error))

    def remove_definition(self):
        index = self.channel_list.currentRow()
        if index >= 0:
            self.model.remove(index)
            self._updated()

    def save_definitions(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save local computed channels", "", "JSON (*.json)")
        if not path:
            return
        output = QSaveFile(path)
        try:
            data = self.model.dumps().encode("utf-8")
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if output.write(data) != len(data) or not output.commit():
                raise OSError(output.errorString())
            self.message.setText("Local computed channels saved.")
        except (ValueError, OSError) as error:
            output.cancelWriting()
            self.message.setText(str(error))

    def load_definitions(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load local computed channels", "", "JSON (*.json)")
        if not path:
            return
        try:
            # Bounded read even if the file grows or is not a regular file.
            with Path(path).open("rb") as source:
                data = source.read(32769)
            if len(data) > 32768:
                raise ValueError("Computed channel file exceeds 32 KiB.")
            self.model.loads(data.decode("utf-8"))
            self._updated()
        except (ValueError, OSError) as error:
            self.message.setText(str(error))
