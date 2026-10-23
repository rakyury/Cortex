"""Isolated C start-dispatcher UI; never receives a CAN client or output API."""
from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox,
    QSpinBox, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
)


def create_backend(**kwargs):
    # Compilation is deliberately lazy and only performed inside the worker.
    from ..start_simulation import StartSimulation
    return StartSimulation(**kwargs)


class SimulationWorker(QObject):
    result = pyqtSignal(object)
    failed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, factory):
        super().__init__()
        self.factory = factory
        self.session = None

    @pyqtSlot(str, object)
    def invoke(self, operation, arguments):
        try:
            if operation == 'close':
                if self.session is not None:
                    self.session.close()
                return
            if operation == 'create':
                self.session = self.factory(**arguments)
                result = self.session.snapshot()
            else:
                if self.session is None:
                    raise RuntimeError('Start the simulation first.')
                result = getattr(self.session, operation)(*arguments)
            self.result.emit(result)
        except Exception as exc:
            # Process/compile failures belong in the UI, never an uncaught Qt slot.
            self.failed.emit(str(exc))
            if self.session is not None:
                try:
                    self.session.close()
                except Exception as close_error:
                    self.failed.emit(f'{exc}; cleanup: {close_error}')
            self.session = None
        finally:
            if operation == 'close':
                self.session = None
                self.closed.emit()


class StartSimulationPanel(QWidget):
    command = pyqtSignal(str, object)
    shutdown_finished = pyqtSignal()

    def __init__(self, parent=None, backend_factory=None):
        super().__init__(parent)
        self._factory = backend_factory or create_backend
        self._thread = self._worker = None
        self._busy = self._active = self._stopping = False
        self.snapshot = None
        layout = QVBoxLayout(self)
        self.notice = QLabel('SIMULATION · fictional load limits, not approved board ratings.\n'
            'STARTING / ON are virtual states. Physical-output simulation: OFF · HIL blocked · no CAN commands.\n'
            '¹ Currents and confirmations below are generated fixtures, not hardware measurements.')
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet('color:#e7a649')
        layout.addWidget(self.notice)
        configuration = QGridLayout()
        self.profile = QComboBox()
        self.profile.addItem('Synapse14 · 14 channels', 14)
        self.profile.addItem('R2 PDM24 · 24 channels', 24)
        self.slots = QComboBox()
        for count in (2, 3, 4):
            self.slots.addItem(str(count), count)
        self.total_budget = QSpinBox()
        self.total_budget.setRange(20, 1000)
        self.total_budget.setValue(300)
        self.total_budget.setSuffix(' A')
        self.group_budget = QSpinBox()
        self.group_budget.setRange(10, 1000)
        self.group_budget.setValue(200)
        self.group_budget.setSuffix(' A')
        for column, (title, control) in enumerate((('Profile', self.profile), ('Start slots', self.slots),
                ('Fixture total budget', self.total_budget), ('Fixture budget / group', self.group_budget))):
            control.setAccessibleName(title)
            configuration.addWidget(QLabel(title), 0, column)
            configuration.addWidget(control, 1, column)
        layout.addLayout(configuration)
        lifecycle = QHBoxLayout()
        self.start_btn = QPushButton('Start simulation')
        self.stop_btn = QPushButton('Stop simulation')
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.shutdown)
        lifecycle.addWidget(self.start_btn)
        lifecycle.addWidget(self.stop_btn)
        self.summary = QLabel('No simulation running. Hardware connection is not required.')
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addLayout(lifecycle)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(['Channel', 'Queue', 'Virtual state', 'Delay / refusal',
            'Reserved A', 'Measured A¹', 'Group', 'Priority', 'Steady A¹', 'Start A¹'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(210)
        self.table.setAccessibleName('Simulated start queue and budgets')
        layout.addWidget(self.table)
        self.table.itemSelectionChanged.connect(self._controls)
        actions = QGridLayout()
        self.action_buttons = {}
        for i, (operation, title) in enumerate((('request', 'Request selected'), ('request_all', 'Request all'),
                ('complete', 'Complete start'), ('fault', 'Inject fault'), ('confirm_off', 'Confirm OFF'),
                ('cancel', 'Cancel selected'), ('clear_fault', 'Clear fault'), ('reset', 'Reset dispatcher'))):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, op=operation: self.perform(op))
            self.action_buttons[operation] = button
            actions.addWidget(button, i // 4, i % 4)
        self.step_ms = QSpinBox()
        self.step_ms.setRange(1, 60000)
        self.step_ms.setValue(10)
        self.step_ms.setSuffix(' ms')
        self.step_ms.setAccessibleName('Simulation time step')
        self.step_btn = QPushButton('Advance time')
        self.step_btn.clicked.connect(lambda: self.perform('step'))
        actions.addWidget(self.step_ms, 2, 0)
        actions.addWidget(self.step_btn, 2, 1)
        layout.addLayout(actions)
        self.message = QLabel('¹ All currents are explicit simulation fixtures. New feedback is applied on manual steps.')
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self._controls()

    def _controls(self):
        running = self._thread is not None
        for control in (self.profile, self.slots, self.total_budget, self.group_budget, self.start_btn):
            control.setEnabled(not running)
        self.stop_btn.setEnabled(running and not self._stopping)
        usable = self._active and not self._busy and not self._stopping
        selected = self.table.currentRow() >= 0
        for name, button in self.action_buttons.items():
            button.setEnabled(usable and (selected or name in ('request_all', 'reset')))
        self.step_btn.setEnabled(usable)
        self.step_ms.setEnabled(usable)

    def start(self):
        if self._thread is not None:
            return
        self._busy = True
        self.snapshot = None
        self.table.setRowCount(0)
        self.summary.setText('Preparing the C simulator…')
        self.message.setText('Compilation and simulation commands run in a background worker.')
        self._thread = QThread(self)
        self._worker = SimulationWorker(self._factory)
        self._worker.moveToThread(self._thread)
        self.command.connect(self._worker.invoke)
        self._worker.result.connect(self._received)
        self._worker.failed.connect(self._failed)
        self._worker.closed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._stopped)
        self._thread.start()
        self._controls()
        self.command.emit('create', dict(profile=self.profile.currentData(), slots=self.slots.currentData(),
            total_budget_ma=self.total_budget.value() * 1000, group_budget_ma=self.group_budget.value() * 1000))

    def perform(self, operation):
        if not self._active or self._busy or self._stopping:
            return
        if operation == 'step':
            arguments = (self.step_ms.value(),)
        elif operation in ('request_all', 'reset'):
            arguments = ()
        else:
            row = self.table.currentRow()
            if row < 0 or self.snapshot is None:
                return
            arguments = (self.snapshot['channels'][row]['channel'],)
        self._busy = True
        self.message.setText('Applying simulation action…')
        self._controls()
        self.command.emit(operation, arguments)

    @pyqtSlot(object)
    def _received(self, snapshot):
        if self._stopping:
            return
        if (snapshot.get('simulation_only') is not True or snapshot.get('fixture_only') is not True
                or snapshot.get('hardware_authorized') is not False
                or snapshot.get('physical_outputs_on') != 0
                or snapshot.get('output_backend') != 'simulated_zero_output_backend'):
            self._failed('Unexpected simulation boundary; session stopped.')
            return
        self.snapshot = snapshot
        self._active, self._busy = True, False
        selected = self.table.currentRow()
        channels = snapshot['channels']
        self.table.setRowCount(len(channels))
        for row, channel in enumerate(channels):
            reason = channel['reason'].replace('_', ' ').lower()
            if channel['state'].upper() == 'QUEUED' and (channel.get('queue_position') or 0) > 1:
                reason = 'Waiting behind queue head'
            elif channel['state'].upper() == 'QUEUED' and channel['reason'].upper() == 'OK':
                reason = 'Awaiting manual dispatch step'
            measured = channel.get('measured_ma')
            values = (channel['channel'], channel.get('queue_position') or '—', channel['state'],
                reason, f"{channel['reserve_ma'] / 1000:.3f}",
                '—' if measured is None else f'{measured / 1000:.3f}', channel['group'], channel['priority'],
                f"{channel['continuous_ma'] / 1000:.3f}", f"{channel['start_ma'] / 1000:.3f}")
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        if channels:
            self.table.selectRow(max(0, min(selected, len(channels) - 1)))
        groups = ' · '.join(f"Group {g['id']}: {g['reserved_ma'] / 1000:.3f} / {g['budget_ma'] / 1000:.3f} A"
                            for g in snapshot['groups'])
        self.summary.setText(f"Profile {snapshot['profile']} · {snapshot['count']} virtual channels · "
            f"time {snapshot['now_ms']} ms · start slots {snapshot['starts_reserved']} / {snapshot['slots']}\n"
            f"Total reserved {snapshot['total_reserved_ma'] / 1000:.3f} / {snapshot['total_budget_ma'] / 1000:.3f} A · {groups}")
        command = str(snapshot.get('last_command', 'snapshot')).replace('_', ' ').lower()
        result = str(snapshot.get('last_result', 'No action result')).replace('_', ' ').lower()
        self.message.setText(f'Last action: {command} · {result}.\n'
            'Physical-output simulation remains OFF; no hardware control is authorized.')
        self._controls()

    @pyqtSlot(str)
    def _failed(self, error):
        self._active = self._busy = False
        self.snapshot = None
        self.table.setRowCount(0)
        self.summary.setText('Simulation unavailable — no live state is displayed.')
        self.message.setText(f'{error}\nCheck the paired firmware source and C compiler, then start again.')
        self.shutdown()

    def shutdown(self):
        """Queue bounded cleanup without blocking the GUI; True if already stopped."""
        if self._thread is None:
            return True
        if not self._stopping:
            self._stopping = True
            self._active = False
            self._controls()
            self.command.emit('close', None)
        return False

    @pyqtSlot()
    def _stopped(self):
        thread = self._thread
        self._thread = self._worker = None
        self._active = self._busy = self._stopping = False
        self.snapshot = None
        self.table.setRowCount(0)
        if not self.summary.text().startswith('Simulation unavailable'):
            self.summary.setText('Simulation stopped. No live state is displayed.')
        if thread is not None:
            thread.deleteLater()
        self._controls()
        self.shutdown_finished.emit()
