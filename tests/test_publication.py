"""Regression assertions for the pre-publication stress findings."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from matplotlib.figure import Figure
import analysis
import config
import main
import solver_process
import xfoil_runner as runner


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='test_publication_', dir=config.PROJECT_DIR)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(config.PROJECT_DIR))
        patches = [patch.object(config, 'OUTPUT_DIR', self.root / 'output'),
                   patch.multiple(config, MAX_ITERATIONS=100, PANEL_NODES=160, TIMEOUT_SECONDS=5.)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.silence = contextlib.redirect_stdout(io.StringIO())
        self.silence.__enter__()
        self.addCleanup(self.silence.__exit__, None, None, None)

    def test_atomic_save_preserves_original_after_serialization_and_replace_failures(self):
        path = self.root / 'settings.json'
        original = '{"max_iterations": 177, "panel_nodes": 180}'
        for target in ('dump', 'replace'):
            path.write_text(original)
            mock = patch.object(config.json if target == 'dump' else config.os, target,
                                side_effect=OSError('simulated disk failure'))
            with patch.object(config, 'SETTINGS_FILE_PATH', path), mock:
                self.assertFalse(config.save_settings())
            self.assertEqual(path.read_text(), original)
            self.assertFalse(list(self.root.glob('*.tmp')))

    def test_bom_settings(self):
        path = self.root / 'settings.json'
        path.write_text('\ufeff{"max_iterations":177,"panel_nodes":180}', encoding='utf-8')
        with patch.object(config, 'SETTINGS_FILE_PATH', path):
            config.load_settings()
        self.assertEqual((config.MAX_ITERATIONS, config.PANEL_NODES), (177, 180))

    def test_numeric_coordinate_header_and_bad_body(self):
        path = self.root / 'coords.dat'
        path.write_text('2412\n1 0\n0 .1\n1 -.1\n')
        self.assertEqual(analysis.read_coordinates(path).shape, (3, 2))
        path.write_text('2412\n1 0\n0\n1 -.1\n')
        with self.assertRaises(ValueError):
            analysis.read_coordinates(path)

    def test_malformed_rows_are_counted_and_retained_as_partial(self):
        polar = self.root / 'polar.txt'
        polar.write_text('\n'*12 + '0 .1 .01 0 0\n1 ******** .02 0 0\n2 .3 .01 0 0\n')
        data = analysis.parse_polar_data(polar)
        self.assertEqual(data['discarded_rows'], 1)
        np.testing.assert_array_equal(data['alpha'], [0, 2])
        metrics = analysis.save_analysis_report_file(data, self.root, 'test', 300000, 0, 2, 1)
        self.assertEqual(metrics['status'], 'partial')
        self.assertIn('Discarded polar rows: 1', (self.root / 'Analysis_Report.txt').read_text())

    def test_subprecision_angles_and_huge_numbers_rejected_before_launch(self):
        for inputs in [(300000, 0, .002, .0001), (300000, .0001, 2, 1),
                       (10**250, 0, 2, 1), (10**40, 0, 2, 1), (300000, 100000000, 100000001, 1)]:
            with patch.object(runner, 'run_solver') as run, self.assertRaises(ValueError):
                runner.run_xfoil_naca('2412', *inputs)
            run.assert_not_called()
        self.assertFalse((self.root/'output').exists())

    def test_derivative_unavailable_for_rounded_duplicate_angles_or_tiny_span(self):
        for angles in ([0, 0, .001], [0, .001, .002]):
            data = {'alpha': np.array(angles), 'CL': np.array([.1, .1001, .1002]),
                    'CD': np.array([.01]*3), 'CL/CD': np.array([10, 10.01, 10.02])}
            self.assertIsNone(analysis.calculate_airfoil_metrics(data, 'small')['Lift_Slope_per_rad'])

    def test_log_limit_stops_and_reaps_a_noisy_process(self):
        result = solver_process.run_solver(sys.executable, self.root,
                     'import sys\nwhile True: sys.stdout.buffer.write(b"x"*65536)\n', 5,
                     self.root/'log', max_log_bytes=4096)
        self.assertIn('log exceeded', result['error'])
        self.assertLessEqual((self.root/'log').stat().st_size, 4096)
        self.assertNotEqual(result['returncode'], 0)

    def test_timeout_and_cancellation_reap_a_sleeping_process(self):
        for cancel in (None, threading.Event()):
            if cancel is not None:
                timer = threading.Timer(.1, cancel.set)
                timer.start()
                self.addCleanup(timer.join)
            before = time.monotonic()
            result = solver_process.run_solver(sys.executable, self.root, 'import time\ntime.sleep(60)\n',
                                              .2 if cancel is None else 5, self.root/'log', cancel)
            self.assertIn('timed out' if cancel is None else 'cancelled', result['error'])
            self.assertLess(time.monotonic()-before, 3)
            self.assertNotEqual(result['returncode'], 0)

    def test_keyboard_interrupt_reaps_running_process(self):
        original = subprocess.Popen.wait
        seen = []
        def interrupt_once(process, *args, **kwargs):
            if not seen:
                seen.append(process)
                raise KeyboardInterrupt
            return original(process, *args, **kwargs)
        with patch.object(subprocess.Popen, 'wait', interrupt_once), self.assertRaises(KeyboardInterrupt):
            solver_process.run_solver(sys.executable, self.root, 'import time\ntime.sleep(60)\n', 5, self.root/'log')
        self.assertIsNotNone(seen[0].poll())

    def test_plot_failures_are_reported_and_no_figures_leak(self):
        data = {'alpha': np.array([0, 1, 2]), 'CL': np.array([.1, .2, .3]),
                'CD': np.array([.01]*3), 'CL/CD': np.array([10, 20, 30])}
        simulated = analysis.calculate_airfoil_metrics(data, 'NACA2412')
        with patch.object(runner, '_simulate', return_value=simulated), \
             patch.object(Figure, 'savefig', side_effect=OSError('disk full')):
            result = runner.run_xfoil_naca('2412', 300000, 0, 2, 1)
        self.assertTrue(result['success'])
        self.assertEqual(result['artifact_status'], 'incomplete')
        self.assertEqual(len(result['artifact_errors']), 3)
        self.assertFalse(analysis.plt.get_fignums())
        report = next((self.root/'output').glob('*/Analysis_Report.txt')).read_text()
        self.assertIn('Artifact delivery: incomplete', report)
        self.assertIn('CL_vs_Alpha.png', report)
        with patch.object(Figure, 'savefig', side_effect=OSError('disk full')):
            errors = runner.generate_batch_report([simulated], self.root, 300000, 0, 2, 1)
        self.assertEqual(len(errors), 2)
        self.assertFalse(analysis.plt.get_fignums())

    def test_eof_and_interrupt_at_main_and_nested_prompts(self):
        for choices, code in [([EOFError()], 0), (['1', EOFError()], 0),
                              ([KeyboardInterrupt()], 130), (['3', KeyboardInterrupt()], 130)]:
            with patch('builtins.input', side_effect=choices), patch.object(config, 'load_settings'):
                self.assertEqual(main.cli(), code)
        result = subprocess.run([sys.executable, str(config.PROJECT_DIR/'main.py')], input='',
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
