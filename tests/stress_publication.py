"""Bounded publication audit. Run directly; writes a new evidence folder, no app edits."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from matplotlib.figure import Figure
import analysis
import config
import xfoil_runner as runner


def case_run(case):
    label, code, rey, start, end, inc, panels, iterations, timeout, out = case
    before = time.monotonic()
    try:
        result = runner._simulate(label, rey, start, end, inc, out,
                                  {'panel_nodes': panels, 'max_iterations': iterations, 'timeout_seconds': timeout},
                                  naca_code=code)
        result.pop('polar_data', None)
        result.update(seconds=round(time.monotonic()-before, 3), label=label)
        result['output_dir'] = str(Path(out).relative_to(ROOT))
        return result
    except Exception as exc:
        return {'label': label, 'exception': repr(exc), 'seconds': round(time.monotonic()-before, 3)}


def main():
    evidence = Path(tempfile.mkdtemp(prefix='publication_stress_', dir=ROOT))
    config.OUTPUT_DIR = evidence / 'reports'
    config.TIMEOUT_SECONDS = 8.
    cases = []
    def add(label, code='2412', rey=300000, start=0, end=2, inc=1, panels=160, iterations=100, timeout=8.):
        cases.append((label, code, rey, start, end, inc, panels, iterations, timeout, str(evidence/label)))
    for code in ('0012', '2412', '4415'):
        for rey in (1000, 30000, 300000, 3000000):
            add(f'matrix_{code}_Re{rey}', code, rey, -10, 20, 1)
    for panels in (10, 20, 300, 494):
        add(f'panels_{panels}', panels=panels)
    for code in ('0000', '2012', '9999'):
        add(f'code_{code}', code=code)
    add('tiny_steps', end=.002, inc=.0001)
    add('rounding_half', end=.3, inc=.2)
    add('rounding_decimal', start=-.3, end=.3, inc=.1)
    add('max_800', end=.799, inc=.001)
    add('large_alpha', start=100000000., end=100000001., inc=1)
    add('large_reynolds', rey=10**40)
    add('small_reynolds', rey=1)
    add('timeout', timeout=.001)
    with ProcessPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(case_run, cases))
    (evidence/'solver_results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')

    probes = {}
    base = evidence/'rounding_decimal'/'coordinates.dat'
    coords = analysis.read_coordinates(base)
    for label, points in [('unit', coords), ('scaled2', coords*2), ('offset2', coords+[2, 0]),
                          ('reversed', coords[::-1]), ('degenerate', np.zeros((3, 2)))]:
        path = evidence/f'{label}.dat'
        np.savetxt(path, points, header=label, comments='')
        result = runner._simulate(label, 300000, 0, 2, 1, evidence/f'custom_{label}',
                                  {'panel_nodes': 160, 'max_iterations': 100, 'timeout_seconds': 8}, coords_file_path=path)
        result.pop('polar_data', None)
        result['output_dir'] = str(Path(result['output_dir']).relative_to(ROOT))
        probes[f'custom_{label}'] = result

    # A single nonnumeric row should not silently erase surrounding useful solver rows.
    polar = evidence/'overflow_fixture.txt'
    polar.write_text('\n'*12 + '0 .1 .01 0 0\n1 ******** .02 0 0\n2 .3 .01 0 0\n')
    probes['overflow_row_parse'] = str(analysis.parse_polar_data(polar))
    for label, contents in [('numeric_title', '2412\n1 0\n0 .1\n1 -.01\n'),
                             ('bom_config', '\ufeff{"max_iterations": 177, "panel_nodes": 180}')]:
        file = evidence/f'{label}.txt'
        file.write_text(contents, encoding='utf-8')
        try:
            if label == 'numeric_title':
                probes[label] = analysis.read_coordinates(file).tolist()
            else:
                with patch.object(config, 'SETTINGS_FILE_PATH', file):
                    config.load_settings()
                    probes[label] = config.snapshot_settings()
        except Exception as exc:
            probes[label] = repr(exc)

    # Persisted settings must survive an interrupted write.
    path = evidence/'write_failure_config.json'
    path.write_text('{"max_iterations": 177, "panel_nodes": 180}')
    with patch.object(config, 'SETTINGS_FILE_PATH', path), patch.object(config.json, 'dump', side_effect=OSError('simulated disk full')):
        probes['save_failure_return'] = config.save_settings()
    probes['settings_after_write_failure'] = path.read_text()

    # Exercise artifact-write errors without modifying the application.
    config.TIMEOUT_SECONDS = 8.
    with patch.object(Figure, 'savefig', side_effect=OSError('simulated image write failure')):
        try:
            result = runner.run_xfoil_naca('2412', 300000, 0, 2, 1)
            probes['plot_failure_return'] = result['artifact_status']
            probes['plot_errors'] = result['artifact_errors']
        except Exception as exc:
            probes['plot_failure_exception'] = repr(exc)
    probes['figures_leaked_after_failure'] = len(analysis.plt.get_fignums())
    analysis.plt.close('all')
    probes['report_status_after_plot_failure'] = [p.read_text().split('Status: ')[1].splitlines()[0]
                                                 for p in config.OUTPUT_DIR.glob('*/Analysis_Report.txt')]

    # A valid finite Reynolds number can exceed a filesystem component's length.
    try:
        config.validate_simulation_inputs(10**250, 0., 2., 1.)
        runner._new_output_dir('NACA2412', 10**250, 0., 2., 1.)
        probes['huge_re_output_path'] = 'accepted'
    except Exception as exc:
        probes['huge_re_output_path'] = repr(exc)
    startup = subprocess.run([sys.executable, str(ROOT/'main.py')], input='', capture_output=True, text=True, timeout=20)
    probes['cli_eof'] = {'exit': startup.returncode, 'stderr': startup.stderr}

    # Repeatability under a larger batch and the cost of figure legends.
    config.MAX_ITERATIONS, config.PANEL_NODES = 100, 160
    before = time.monotonic()
    repeated = runner.run_xfoil_batch(['2412']*24, 300000, 0, 2, 1, max_workers=4)
    probes['batch24'] = {'seconds': round(time.monotonic()-before, 3), 'successes': sum(r['success'] for r in repeated),
                         'unique_dirs': len({r['output_dir'] for r in repeated}),
                         'unique_max_lift': sorted({r.get('CL_max') for r in repeated if r['success']})}
    probes['leftover_work_dirs'] = [str(p.relative_to(evidence)) for p in evidence.rglob('work_*')]
    probes['artifact_bytes'] = sum(p.stat().st_size for p in evidence.rglob('*') if p.is_file())
    assert probes['plot_failure_return'] == 'incomplete'
    assert probes['figures_leaked_after_failure'] == 0
    assert probes['bom_config']['panel_nodes'] == 180
    assert json.loads(probes['settings_after_write_failure'])['max_iterations'] == 177
    assert len(probes['numeric_title']) == 3
    assert probes['cli_eof']['exit'] == 0
    assert probes['batch24']['unique_dirs'] == 24
    assert probes['batch24']['successes'] == 24
    assert len(probes['batch24']['unique_max_lift']) == 1
    assert not probes['leftover_work_dirs']
    assert abs(probes['custom_unit']['CL_max'] - probes['custom_scaled2']['CL_max']) < .0005
    assert abs(probes['custom_unit']['CL_max'] - probes['custom_offset2']['CL_max']) < .0005
    assert len(analysis.parse_polar_data(polar)['alpha']) == 2
    assert all(path.stat().st_size <= config.MAX_LOG_BYTES for path in evidence.rglob('xfoil.log'))
    (evidence/'probes.json').write_text(json.dumps(probes, indent=2), encoding='utf-8')
    print('EVIDENCE:', evidence)
    print('MATRIX:', {status: sum(r.get('status') == status for r in results)
                      for status in ('complete', 'partial', 'failed')})
    print('BATCH24:', probes['batch24'])
    print('Full results and reproduction evidence are saved in solver_results.json and probes.json.')


if __name__ == '__main__':
    main()
