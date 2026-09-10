"""Generate the curated GitHub examples with a locally installed XFOIL."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import config
import xfoil_runner as runner


def generate():
    destination = ROOT/'examples'
    destination.mkdir(exist_ok=True)
    summary = {'solver': 'XFOIL 6.99',
               'solver_sha256': hashlib.sha256(Path(runner.resolve_xfoil()).read_bytes()).hexdigest(),
               'cases': {}}
    previous = (config.OUTPUT_DIR, config.MAX_ITERATIONS, config.PANEL_NODES, config.TIMEOUT_SECONDS)
    with tempfile.TemporaryDirectory(prefix='example_work_', dir=ROOT) as temp:
        work = Path(temp).resolve()
        if not work.is_relative_to(ROOT):
            raise ValueError('Temporary examples directory escaped the project.')
        config.OUTPUT_DIR = work/'output'
        config.MAX_ITERATIONS, config.PANEL_NODES, config.TIMEOUT_SECONDS = 300, 240, 30.
        try:
            def collect(label, result, conditions):
                target = destination/label
                target.mkdir(exist_ok=True)
                source = Path(result['output_dir'])
                for name in ('Analysis_Report.txt', 'polar.txt', 'coordinates.dat', 'commands.txt',
                             'Airfoil_Shape.png', 'CL_vs_Alpha.png', 'CL_vs_CD_Polar.png'):
                    if (source/name).is_file():
                        shutil.copy2(source/name, target/name)
                if not result['success']:
                    shutil.copy2(source/'xfoil.log', target/'xfoil.log')
                summary['cases'][label] = {
                    'airfoil': result['Airfoil'], 'settings': result['settings'], 'conditions': conditions,
                    'status': result['status'], 'artifact_status': result['artifact_status'],
                    'converged_points': result['converged_points'], 'requested_points': result['requested_points'],
                    'missing_angles': result['missing_angles'], 'error': result['error'],
                    'CL_max': result.get('CL_max'), 'CL/CD_max': result.get('CL/CD_max'), 'CD_min': result.get('CD_min')}
                return target

            conditions = {'reynolds': 300000, 'alpha_start': -4, 'alpha_end': 12, 'alpha_increment': .5}
            single = runner.run_xfoil_naca('2412', 300000, -4, 12, .5)
            if not single['success'] or single['artifact_status'] != 'complete':
                raise RuntimeError('Single example did not produce its artifacts.')
            collect('naca2412', single, conditions)

            # The input is generated locally, not copied from a third-party airfoil dataset.
            points = np.loadtxt(Path(single['output_dir'])/'coordinates.dat', skiprows=1)
            custom_input = work/'custom_scaled_shifted.dat'
            np.savetxt(custom_input, points*2 + [2, .5], header='Scaled and translated NACA 2412', comments='')
            custom = runner.run_xfoil_custom(custom_input, 300000, -4, 12, .5)
            if not custom['success'] or custom['artifact_status'] != 'complete':
                raise RuntimeError('Custom example did not produce its artifacts.')
            target = collect('custom_normalization', custom, conditions)
            shutil.copy2(custom_input, target/'input.dat')

            results = runner.run_xfoil_batch(['0012', '2412', '4415'], 300000, -4, 12, .5, max_workers=3)
            if not all(r['success'] and not r['batch_artifact_errors'] for r in results):
                raise RuntimeError('Batch example did not produce its artifacts.')
            batch_dir = Path(results[0]['output_dir']).parent
            target = destination/'batch_comparison'
            target.mkdir(exist_ok=True)
            report = (batch_dir/'Batch_Optimization_Report.txt').read_text(encoding='utf-8')
            for result in results:
                job = Path(result['output_dir'])
                relative = Path('jobs')/job.name
                report = report.replace(str(job), relative.as_posix())
                job_target = target/relative
                job_target.mkdir(parents=True, exist_ok=True)
                for name in ('polar.txt', 'coordinates.dat', 'commands.txt'):
                    shutil.copy2(job/name, job_target/name)
            (target/'Batch_Optimization_Report.txt').write_text(report, encoding='utf-8')
            for name in ('Batch_CL_vs_Alpha_Comparison.png', 'Batch_Efficiency_Comparison.png'):
                shutil.copy2(batch_dir/name, target/name)
            summary['cases']['batch_comparison'] = {
                'settings': config.snapshot_settings(), 'conditions': conditions,
                'airfoils': [{'name': r['Airfoil'], 'status': r['status'], 'CL_max': r['CL_max'],
                             'CL/CD_max': r['CL/CD_max'], 'CD_min': r['CD_min'],
                             'converged_points': r['converged_points'], 'requested_points': r['requested_points']}
                            for r in sorted(results, key=lambda r: r['Airfoil'])]}

            config.MAX_ITERATIONS, config.PANEL_NODES = 100, 160
            partial = runner.run_xfoil_naca('2412', 300000, 0, 2, 1)
            if partial['status'] != 'partial':
                raise RuntimeError('Expected a naturally partial sweep with this solver/settings; inspect before updating examples.')
            collect('partial_convergence', partial,
                    {'reynolds': 300000, 'alpha_start': 0, 'alpha_end': 2, 'alpha_increment': 1})

            invalid = work/'invalid_geometry.dat'
            invalid.write_text('Invalid example: identical points\n0 0\n0 0\n0 0\n')
            failed = runner.run_xfoil_custom(invalid, 300000, 0, 2, 1)
            if failed['status'] != 'failed':
                raise RuntimeError('Invalid geometry was not rejected.')
            target = collect('failed_input', failed,
                             {'reynolds': 300000, 'alpha_start': 0, 'alpha_end': 2, 'alpha_increment': 1})
            shutil.copy2(invalid, target/'input.dat')
        finally:
            config.OUTPUT_DIR, config.MAX_ITERATIONS, config.PANEL_NODES, config.TIMEOUT_SECONDS = previous
    (destination/'index.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    generate()
