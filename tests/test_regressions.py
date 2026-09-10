import contextlib
import io
import json
import math
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import analysis
import config
import main
import xfoil_runner as runner


def polar_text(rows):
    return '\n' * 12 + rows


def linear_data(alpha=(-2., -1., 0.), cl=(-.2, -.1, 0.)):
    return {'alpha': np.array(alpha), 'CL': np.array(cl), 'CD': np.full(len(cl), .01),
            'CM': np.zeros(len(cl)), 'CL/CD': np.array(cl) / .01}


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='test_', dir=config.PROJECT_DIR)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(config.PROJECT_DIR))
        self.output_patch = patch.object(config, 'OUTPUT_DIR', self.root / 'output')
        self.output_patch.start()
        self.addCleanup(self.output_patch.stop)
        self.settings_patch = patch.multiple(config, MAX_ITERATIONS=100, PANEL_NODES=160,
                                             TIMEOUT_SECONDS=120.)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.capture = contextlib.redirect_stdout(io.StringIO())
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def test_empty_short_and_malformed_polars(self):
        for content in ('', polar_text(''), polar_text('0 1\n'), polar_text('bad row\n')):
            with self.subTest(content=content):
                path = self.root / 'polar.txt'
                path.write_text(content)
                self.assertIsNone(analysis.parse_polar_data(path))

    def test_one_row_and_invalid_consumed_columns(self):
        path = self.root / 'polar.txt'
        path.write_text(polar_text('0 -.2 .01 0 0\nnan .1 .01 0 0\n1 .2 .01 0 inf\n2 .2 0 0 0\n'))
        data = analysis.parse_polar_data(path)
        np.testing.assert_array_equal(data['alpha'], [0])
        np.testing.assert_array_equal(data['CL/CD'], [-20])

    def test_variable_header(self):
        path = self.root / 'polar.txt'
        path.write_text('XFOIL\nalpha CL CD CDp CM\n------ ------\n1 .1 .01 0 0\n')
        self.assertEqual(analysis.parse_polar_data(path)['CL'][0], .1)

    def test_slope_conversion_zero_lift_and_no_stall_claim(self):
        data = linear_data()
        result = analysis.save_analysis_report_file(data, self.root, 'zero', 300000, -2, 0, 1)
        self.assertTrue(result['success'])
        self.assertAlmostEqual(result['Lift_Slope_per_rad'], .1 * 180 / math.pi)
        self.assertIsNone(result['alpha_stall'])
        report = (self.root / 'Analysis_Report.txt').read_text()
        self.assertIn('0.1000 per degree', report)
        self.assertIn('Stall angle: Undetermined', report)
        self.assertNotIn('ANALYSIS FAILED', report)

    def test_two_point_slope_and_unavailable_slope(self):
        metrics = analysis.calculate_airfoil_metrics(linear_data((-1., 0.), (-.1, 0.)), 'two')
        self.assertAlmostEqual(metrics['Lift_Slope_per_rad'], .1 * 180 / math.pi)
        metrics = analysis.calculate_airfoil_metrics(linear_data((10., 11.), (1., 1.1)), 'outside')
        self.assertIsNone(metrics['Lift_Slope_per_rad'])

    def test_partial_coverage_matches_aseq_rounding(self):
        coverage = analysis.coverage(linear_data((0., 2.), (.1, .3)), 0, 2, 1)
        self.assertEqual(coverage['missing_angles'], [1])
        self.assertEqual(config.requested_angles(0, 1, .6), [0, .6, 1.2])
        coverage = analysis.coverage(linear_data((0.,), (.1,)), 0, .001, .0005)
        self.assertEqual(coverage['converged_points'], 1)

    def test_negative_candidates_sort_and_all_failed(self):
        results = [analysis.calculate_airfoil_metrics(linear_data(cl=(-.4, -.3, -.2)), 'Z'),
                   analysis.calculate_airfoil_metrics(linear_data(cl=(-.3, -.2, -.1)), 'A')]
        with patch.object(analysis, 'plot_batch_results', return_value={}):
            runner.generate_batch_report(results, self.root, 300000, -2, 0, 1)
            report = (self.root / 'Batch_Optimization_Report.txt').read_text()
            self.assertLess(report.index('A |'), report.index('Z |'))
            self.assertIn('Max Eff: A', report)
            runner.generate_batch_report([analysis.calculate_airfoil_metrics(None, 'failed')],
                                         self.root, 300000, -2, 0, 1)
        report = (self.root / 'Batch_Optimization_Report.txt').read_text()
        self.assertIn('No valid candidates', report)
        self.assertNotIn('9999', report)

    def test_plain_and_labeled_coordinates_preserve_first_point(self):
        for header in ('', 'NACA test\n'):
            path = self.root / 'coords.dat'
            path.write_text(header + '1 0\n0 .1\n1 -.01\n')
            np.testing.assert_array_equal(analysis.read_coordinates(path), [[1, 0], [0, .1], [1, -.01]])

    def test_invalid_settings_restore_all_defaults(self):
        settings_path = self.root / 'config.json'
        for value in ({'max_iterations': -1, 'panel_nodes': 'bad'}, [], {'panel_nodes': 500},
                      {'timeout_seconds': float('inf')}, {'max_iterations': True}):
            settings_path.write_text(json.dumps(value))
            config.MAX_ITERATIONS, config.PANEL_NODES = 300, 240
            with patch.object(config, 'SETTINGS_FILE_PATH', settings_path):
                config.load_settings()
            self.assertEqual(config.snapshot_settings(), config.DEFAULT_SETTINGS)

    def test_settings_round_trip(self):
        config.MAX_ITERATIONS, config.PANEL_NODES, config.TIMEOUT_SECONDS = 177, 180, 30.
        with patch.object(config, 'SETTINGS_FILE_PATH', self.root / 'config.json'):
            self.assertTrue(config.save_settings())
            config.MAX_ITERATIONS = 100
            config.load_settings()
        self.assertEqual(config.snapshot_settings(), {'max_iterations': 177, 'panel_nodes': 180, 'timeout_seconds': 30.})

    def test_nonfinite_and_panel_validation(self):
        for inputs in ((300000, float('nan'), 10., 1.), (300000, 0., 10., float('inf')),
                       (0, 0, 10, 1), (300000, 0, 10, 0), (300000, 10, 0, 1)):
            with self.assertRaises(ValueError):
                config.validate_simulation_inputs(*inputs)
        for panels in (0, 2, 159, 496, '160', True):
            with self.assertRaises(ValueError):
                config.validate_settings({'panel_nodes': panels})
        self.assertEqual(config.validate_settings({'panel_nodes': 494})['panel_nodes'], 494)

    def test_cli_reprompts_nonfinite_and_invalid_batch_tokens(self):
        with patch('builtins.input', side_effect=['300000', 'nan', '10', '1', '300000', '0', '2', '1']):
            self.assertEqual(main.get_simulation_inputs(), (300000, 0., 2., 1.))
        with patch('builtins.input', side_effect=['2412,typo,4415', '2412,4415']), \
             patch.object(main, 'get_simulation_inputs', return_value=(300000, 0, 2, 1)), \
             patch.object(runner, 'run_xfoil_batch') as run:
            main.batch_analysis_menu()
        self.assertEqual(run.call_args.args[0], ['2412', '4415'])

    def test_custom_menu_rejects_directory(self):
        coords = self.root / 'test.dat'
        coords.write_text('1 0\n0 0\n1 -.1\n')
        with patch('builtins.input', side_effect=[str(self.root), str(coords)]), \
             patch.object(main, 'get_simulation_inputs', return_value=(300000, 0, 2, 1)), \
             patch.object(runner, 'run_xfoil_custom') as run:
            main.custom_analysis_menu()
        self.assertEqual(run.call_args.args[0], str(coords))

    def test_output_reservation_is_atomic(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(lambda _: runner._new_output_dir('same', 300000, 0, 2, 1), range(8)))
        self.assertEqual(len(set(paths)), 8)

    def test_timeout_kills_and_reaps_and_writes_failure_report(self):
        with patch.object(runner, 'resolve_xfoil', return_value='xfoil'), \
             patch.object(runner, 'run_solver', return_value={'returncode': -9, 'error': 'XFOIL timed out after 120 seconds.'}):
            result = runner.run_xfoil_naca('2412', 300000, 0, 2, 1)
        self.assertFalse(result['success'])
        path = Path(result['output_dir'])
        self.assertIn('timed out', (path / 'Analysis_Report.txt').read_text())
        self.assertFalse(list(path.glob('work_*')))

    def test_nonzero_exit_rejects_existing_polar(self):
        def execute(executable, work, *args):
            (Path(work) / 'polar.txt').write_text(polar_text('0 .1 .01 0 0\n'))
            return {'returncode': 7, 'error': 'XFOIL exited with code 7.'}
        with patch.object(runner, 'resolve_xfoil', return_value='xfoil'), patch.object(runner, 'run_solver', side_effect=execute):
            result = runner.run_xfoil_naca('2412', 300000, 0, 2, 1)
        self.assertFalse(result['success'])
        self.assertIn('code 7', result['error'])

    def test_missing_executable_creates_failure_report(self):
        with patch.object(runner, 'resolve_xfoil', side_effect=FileNotFoundError('missing executable')):
            result = runner.run_xfoil_naca('2412', 300000, 0, 2, 1)
        self.assertFalse(result['success'])
        self.assertIn('missing executable', (Path(result['output_dir']) / 'Analysis_Report.txt').read_text())

    def test_partial_success_retains_diagnostics_and_settings(self):
        config.MAX_ITERATIONS, config.PANEL_NODES = 177, 180
        def execute(executable, work, *args):
            (Path(work) / 'polar.txt').write_text(polar_text('0 -.1 .01 0 0\n2 0 .01 0 0\n'))
            return {'returncode': 0, 'error': ''}
        with patch.object(runner, 'resolve_xfoil', return_value='xfoil'), patch.object(runner, 'run_solver', side_effect=execute):
            result = runner._simulate('NACA2412', 300000, 0, 2, 1, self.root, config.snapshot_settings(), naca_code='2412')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['missing_angles'], [1])
        script = (self.root / 'commands.txt').read_text()
        self.assertIn('ITER 177', script)
        self.assertIn('N 180', script)

    def test_worker_exception_preserves_success_and_duplicate_job_isolation(self):
        args_seen = []
        class Executor:
            def __init__(self, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def submit(self, function, args):
                args_seen.append(args)
                future = Future()
                if len(args_seen) == 2:
                    future.set_exception(RuntimeError('worker failed'))
                else:
                    future.set_result(analysis.calculate_airfoil_metrics(linear_data(), 'NACA' + args[0]))
                return future
        config.MAX_ITERATIONS, config.PANEL_NODES = 177, 180
        with patch.object(runner, 'ProcessPoolExecutor', Executor), patch.object(analysis, 'plot_batch_results', return_value={}):
            results = runner.run_xfoil_batch(['2412', '2412', '0012'], 300000, 0, 2, 1)
        self.assertEqual(sum(r['success'] for r in results), 2)
        self.assertEqual(len(results), 3)
        self.assertEqual(len({args[5] for args in args_seen}), 3)
        self.assertTrue(all(args[6]['panel_nodes'] == 180 for args in args_seen))

    def test_missing_dependency_guidance_before_import_traceback(self):
        process = subprocess.run([sys.executable, '-S', str(config.PROJECT_DIR / 'main.py')],
                                 cwd=self.root, capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 1)
        self.assertIn('pip install -r', process.stdout)
        self.assertNotIn('Traceback', process.stderr)


if __name__ == '__main__':
    unittest.main()
