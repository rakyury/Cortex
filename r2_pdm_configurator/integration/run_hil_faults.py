"""Run software HIL-preparation faults and preserve reproducible evidence.

This runner never opens a physical adapter or grants hardware authorization.
The tests compile the real C policy and use an in-process simulated CAN adapter.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest
import xml.etree.ElementTree as ET


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self._started = {}
        self._outcomes = {}

    def startTest(self, test):
        self._started[test.id()] = time.monotonic()
        super().startTest(test)

    def _record(self, test, outcome, detail=''):
        self._outcomes[test.id()] = (outcome, detail)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._outcomes.setdefault(test.id(), ('passed', ''))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, 'failed', self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, 'error', self._exc_info_to_string(err, test))
        # setUpClass/import failures are holders without startTest/stopTest.
        if test.id() not in self._started:
            self._append(test)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, 'skipped', reason)
        if test.id() not in self._started:
            self._append(test)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._record(test, 'expected_failure', self._exc_info_to_string(err, test))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._record(test, 'unexpected_success')

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._record(test, 'failed', self._exc_info_to_string(err, test))

    def _append(self, test):
        outcome, detail = self._outcomes.get(test.id(), ('error', 'Missing outcome'))
        self.records.append(dict(test=test.id(), description=test.shortDescription() or '',
            outcome=outcome, seconds=round(time.monotonic()-self._started.get(test.id(), time.monotonic()), 6),
            detail=detail))

    def stopTest(self, test):
        self._append(test)
        super().stopTest(test)


def command_text(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else 'unavailable'
    except (OSError, subprocess.TimeoutExpired):
        return 'unavailable'


def repository(path):
    if not path:
        return dict(path=None, sha='unavailable', dirty=None)
    path = str(Path(path).resolve())
    status = command_text(['git', '-C', path, 'status', '--porcelain', '--untracked-files=no'])
    return dict(path=path, sha=command_text(['git', '-C', path, 'rev-parse', 'HEAD']),
                dirty=None if status == 'unavailable' else bool(status))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('artifacts/hil-faults'))
    parser.add_argument('--pattern', choices=('test_hil_fault*.py', 'test_*.py'), default='test_hil_fault*.py')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    compiler = os.environ.get('CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
    started = datetime.now(timezone.utc).isoformat()
    suite = unittest.defaultTestLoader.discover(str(root / 'r2_pdm_configurator/integration'),
                                               pattern=args.pattern, top_level_dir=str(root))
    with (args.output/'tests.log').open('w', encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=EvidenceResult).run(suite)
    complete = bool(result.records) and all(r['outcome']=='passed' for r in result.records)
    evidence = dict(schema_version=1, started_utc=started, software_only=True,
                    hardware_tested=False, hardware_authorized=False,
                    scope='Actual C host simulation and Qt; no electrical CAN/ADC/HIL validation',
                    cortex=repository(root), firmware=repository(os.environ.get('PDM_FIRMWARE_ROOT')),
                    compiler=command_text([compiler, '--version']) if compiler else 'unavailable',
                    sanitizers=os.environ.get('PDM_LINK_SANITIZE')=='1',
                    passed=complete, tests_run=result.testsRun, scenarios=result.records)
    (args.output/'scenarios.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    xml = ET.Element('testsuite', name='Software HIL fault preparation', tests=str(len(result.records)),
        failures=str(sum(r['outcome'] not in ('passed','skipped') for r in result.records)),
        skipped=str(sum(r['outcome']=='skipped' for r in result.records)))
    for row in result.records:
        case = ET.SubElement(xml, 'testcase', name=row['test'], time=str(row['seconds']))
        ET.SubElement(case, 'system-out').text = row['description']
        if row['outcome'] != 'passed':
            ET.SubElement(case, 'skipped' if row['outcome']=='skipped' else 'failure',
                          message=row['outcome']).text = row['detail']
    ET.ElementTree(xml).write(args.output/'junit.xml', encoding='utf-8', xml_declaration=True)
    print(f'{len(result.records)} software tests: ' + ('PASS' if complete else 'FAIL / INCOMPLETE'))
    print(f'Evidence: {args.output.resolve()}')
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
