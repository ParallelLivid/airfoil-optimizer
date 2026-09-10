"""Opt in with XFOIL_SMOKE=1; all generated files stay in a private test directory."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import analysis
import config
import xfoil_runner as runner
import numpy as np


@unittest.skipUnless(os.environ.get('XFOIL_SMOKE') == '1', 'set XFOIL_SMOKE=1 to run the real solver')
class XfoilIntegration(unittest.TestCase):
    def test_normalized_geometry_log_cap_and_batch_cancellation(self):
        with tempfile.TemporaryDirectory(prefix='smoke_', dir=config.PROJECT_DIR) as temp:
            root = Path(temp).resolve()
            self.assertTrue(root.is_relative_to(config.PROJECT_DIR))
            with patch.multiple(config, OUTPUT_DIR=root/'output', MAX_ITERATIONS=177,
                                PANEL_NODES=180, TIMEOUT_SECONDS=10.):
                original = runner.run_xfoil_naca('2412', 300000, 0, 2, 1)
                coords = analysis.read_coordinates(Path(original['output_dir'])/'coordinates.dat')
                controls = []
                for index, transformed in enumerate((coords, coords*2, coords+[2., .5])):
                    path = root/f'custom {index}.dat'
                    np.savetxt(path, transformed, header='2412', comments='')
                    result = runner.run_xfoil_custom(path, 300000, 0, 2, 1)
                    self.assertTrue(result['success'], result['error'])
                    self.assertEqual(result['artifact_status'], 'complete')
                    controls.append(result)
                for result in controls[1:]:
                    self.assertAlmostEqual(result['CL_max'], controls[0]['CL_max'], delta=.0005)
                    self.assertAlmostEqual(result['CD_min'], controls[0]['CD_min'], delta=.00005)
                noisy = runner.run_xfoil_naca('2412', 1, 0, 2, 1)
                self.assertFalse(noisy['success'])
                self.assertIn('log exceeded', noisy['error'])
                self.assertLessEqual((Path(noisy['output_dir'])/'xfoil.log').stat().st_size, config.MAX_LOG_BYTES)
                before = time.monotonic()
                with patch.object(runner, 'as_completed', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                    runner.run_xfoil_batch(['2412']*4, 1, 0, 2, 1, max_workers=2)
                self.assertLess(time.monotonic()-before, 10)
                self.assertFalse(list(root.rglob('work_*')))

    def test_real_workflows_and_concurrent_batches(self):
        with tempfile.TemporaryDirectory(prefix='smoke_', dir=config.PROJECT_DIR) as temp:
            root = Path(temp).resolve()
            self.assertTrue(root.is_relative_to(config.PROJECT_DIR))
            with patch.multiple(config, OUTPUT_DIR=root / 'output with spaces', MAX_ITERATIONS=177,
                                PANEL_NODES=180, TIMEOUT_SECONDS=30.):
                single = runner.run_xfoil_naca('2412', 300000, 0, 2, 1)
                self.assertEqual(single['status'], 'complete')
                single_dir = Path(single['output_dir'])
                for name in ('polar.txt', 'coordinates.dat', 'Analysis_Report.txt', 'Airfoil_Shape.png',
                             'CL_vs_Alpha.png', 'CL_vs_CD_Polar.png', 'xfoil.log'):
                    self.assertGreater((single_dir / name).stat().st_size, 0)
                self.assertEqual(len(analysis.read_coordinates(single_dir / 'coordinates.dat')), 180)
                custom_path = root / 'custom airfoil with spaces.dat'
                custom_path.write_bytes((single_dir / 'coordinates.dat').read_bytes())
                custom = runner.run_xfoil_custom(str(custom_path), 300000, 0, 2, 1)
                self.assertEqual(custom['status'], 'complete')
                self.assertEqual(len(analysis.read_coordinates(Path(custom['output_dir']) / 'coordinates.dat')), 180)
                batch = runner.run_xfoil_batch(['2412', '0012', '2412'], 300000, 0, 2, 1, max_workers=2)
                self.assertEqual(len(batch), 3)
                self.assertTrue(all(r['status'] == 'complete' for r in batch))
                self.assertEqual(len({r['output_dir'] for r in batch}), 3)
                for result in batch:
                    self.assertEqual(result['settings']['panel_nodes'], 180)
                    job = Path(result['output_dir'])
                    self.assertIn('ITER 177', (job / 'commands.txt').read_text())
                    self.assertEqual(len(analysis.read_coordinates(job / 'coordinates.dat')), 180)
                batch_dir = Path(batch[0]['output_dir']).parent
                for name in ('Batch_Optimization_Report.txt', 'Batch_CL_vs_Alpha_Comparison.png',
                             'Batch_Efficiency_Comparison.png'):
                    self.assertGreater((batch_dir / name).stat().st_size, 0)
                # Matplotlib isn't thread-safe. Concurrent solver runs still use real process pools.
                with patch.object(analysis, 'plot_batch_results', return_value={}), ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(runner.run_xfoil_batch, ['2412', '2412'], 300000, 0, 2, 1, 2)
                               for _ in range(2)]
                    concurrent = [f.result() for f in futures]
                self.assertTrue(all(r['success'] for batch in concurrent for r in batch))
                self.assertEqual(len({r['output_dir'] for batch in concurrent for r in batch}), 4)
                self.assertFalse(list(root.rglob('work_*')))
            launch = subprocess.run([sys.executable, str(config.PROJECT_DIR / 'main.py')], input='5\n',
                                    capture_output=True, text=True, cwd=root, timeout=20)
            self.assertEqual(launch.returncode, 0, launch.stderr)
            self.assertIn('Goodbye', launch.stdout)


if __name__ == '__main__':
    unittest.main()
