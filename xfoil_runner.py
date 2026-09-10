"""Bounded XFOIL execution with isolated work directories and explicit job settings."""
import os
from pathlib import Path
import re
import shutil
import tempfile
import multiprocessing
import signal
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import config
import analysis
from solver_process import run_solver

_cancel_event = None


def _init_worker(cancel_event):
    global _cancel_event
    _cancel_event = cancel_event
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def resolve_xfoil():
    bundled = config.PROJECT_DIR / ('xfoil.exe' if os.name == 'nt' else 'xfoil')
    if bundled.is_file():
        return str(bundled)
    found = shutil.which('xfoil')
    if found:
        return str(Path(found).resolve())
    raise FileNotFoundError('XFOIL executable not found beside the application or on PATH.')


def _new_output_dir(name, rey_number, alpha_start, alpha_end, alpha_inc):
    config.validate_simulation_inputs(rey_number, alpha_start, alpha_end, alpha_inc)
    root = Path(config.OUTPUT_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[^A-Za-z0-9_-]', '_', name)[:50] or 'airfoil'
    rey, alpha = config._format_rey_alpha(rey_number, alpha_start, alpha_end, alpha_inc)
    # mkdtemp reserves the name atomically, including across application instances.
    path = Path(tempfile.mkdtemp(prefix=f'{safe_name}_{rey}_{alpha}_', dir=root))
    print(f'Created output folder: {path}')
    return path


def _setup_run_environment(airfoil_name, rey_number, alpha_start, alpha_end, alpha_inc):
    path = _new_output_dir(airfoil_name, rey_number, alpha_start, alpha_end, alpha_inc)
    polar, coords = config.get_filenames(path)
    return str(path), polar, coords, path.name


def _setup_batch_environment(rey_number, alpha_start, alpha_end, alpha_inc):
    return str(_new_output_dir('BATCH', rey_number, alpha_start, alpha_end, alpha_inc))


def _failed_result(name, error, settings, output_dir):
    result = analysis.calculate_airfoil_metrics(None, name)
    result.update(error=str(error), settings=dict(settings), output_dir=str(output_dir))
    return result


def _simulate(name, rey_number, alpha_start, alpha_end, alpha_inc, output_dir,
              settings, *, naca_code=None, coords_file_path=None, cancel_event=None):
    """Run one job. Only short relative filenames are sent to the Fortran parser."""
    settings = config.validate_settings(settings)
    config.validate_simulation_inputs(rey_number, alpha_start, alpha_end, alpha_inc)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = _failed_result(name, 'No converged polar data.', settings, output_dir)
    log_path = output_dir / 'xfoil.log'
    try:
        executable = resolve_xfoil()
        # Each invocation owns its temporary directory; cleanup cannot touch another job.
        with tempfile.TemporaryDirectory(prefix='work_', dir=output_dir) as temp:
            work = Path(temp)
            if coords_file_path is not None:
                coords = analysis.read_coordinates(coords_file_path)
                np.savetxt(work / 'input.dat', coords, header='Custom airfoil', comments='')
                load = 'LOAD input.dat'
            else:
                if not config.valid_naca_code(naca_code):
                    raise ValueError('NACA code must contain four ASCII digits.')
                load = f'NACA {naca_code}'
            commands = ['PLOP', 'G', ''] + (['NORM'] if coords_file_path is not None else []) + [load, 'PPAR', f'N {settings["panel_nodes"]}', '', '',
                        'SAVE coordinates.dat', '', 'OPER', f'VISC {rey_number}',
                        f'ITER {settings["max_iterations"]}', 'PACC', 'polar.txt', '',
                        f'ASEQ {alpha_start} {alpha_end} {alpha_inc}', '', 'QUIT', '']
            script = '\n'.join(commands)
            (output_dir / 'commands.txt').write_text(script, encoding='ascii')
            execution = run_solver(executable, work, script, settings['timeout_seconds'], log_path, cancel_event)
            if execution['error']:
                result['error'] = execution['error']
            else:
                data = analysis.parse_polar_data(work / 'polar.txt')
                result = analysis.calculate_airfoil_metrics(data, name)
                result.update(settings=dict(settings), output_dir=str(output_dir))
            result['returncode'] = execution['returncode']
            for filename in ('polar.txt', 'coordinates.dat'):
                if (work / filename).is_file():
                    shutil.copy2(work / filename, output_dir / filename)
    except (OSError, ValueError) as exc:
        result = _failed_result(name, exc, settings, output_dir)
    result.update(analysis.coverage(result.get('polar_data'), alpha_start, alpha_end, alpha_inc))
    if result['success'] and (result['missing_angles'] or result.get('discarded_rows')):
        result['status'] = 'partial'
    if not result['success']:
        with log_path.open('a', encoding='utf-8') as log:
            log.write('\nAutomation failure: ' + result['error'][:400] + '\n')
    print(f'{name}: {result["status"]} ({result["converged_points"]}/{result["requested_points"]} points)'
          + (f' - {result["error"]}' if result['error'] else ''))
    return result


def _run_single(name, rey_number, alpha_start, alpha_end, alpha_inc, **airfoil):
    config.validate_simulation_inputs(rey_number, alpha_start, alpha_end, alpha_inc)
    settings = config.snapshot_settings()
    output_dir = _new_output_dir(name, rey_number, alpha_start, alpha_end, alpha_inc)
    result = _simulate(name, rey_number, alpha_start, alpha_end, alpha_inc,
                       output_dir, settings, **airfoil)
    data = result['polar_data']
    result['artifact_errors'] = {}
    if result['success']:
        result['artifact_errors'].update(analysis.plot_airfoil_shape(output_dir / 'coordinates.dat', output_dir, name))
        result['artifact_errors'].update(analysis.plot_results(data, output_dir, name))
    result['artifact_status'] = 'incomplete' if result['artifact_errors'] else 'complete'
    try:
        analysis.save_analysis_report_file(data, output_dir, name, rey_number, alpha_start,
                                           alpha_end, alpha_inc, settings=settings, result=result)
    except OSError as exc:
        result['artifact_status'] = 'incomplete'
        result['artifact_errors']['Analysis_Report.txt'] = str(exc)
    if result['artifact_errors']:
        print(f'Incomplete artifact delivery: {result["artifact_errors"]}')
    return result


def run_xfoil_naca(naca_code, rey_number, alpha_start, alpha_end, alpha_inc):
    return _run_single(f'NACA{naca_code}', rey_number, alpha_start, alpha_end, alpha_inc,
                       naca_code=naca_code)


def run_xfoil_custom(coords_file_path, rey_number, alpha_start, alpha_end, alpha_inc):
    source = Path(coords_file_path).expanduser().resolve()
    return _run_single(source.stem, rey_number, alpha_start, alpha_end, alpha_inc,
                       coords_file_path=source)


def run_xfoil_for_metrics(naca_code, rey_number, alpha_start, alpha_end, alpha_inc,
                          unique_temp_polar_path, settings=None):
    """Compatibility entry point; every invocation reserves a private sibling job folder."""
    parent = Path(unique_temp_polar_path).resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    job_dir = tempfile.mkdtemp(prefix=f'job_{naca_code}_', dir=parent)
    return _simulate(f'NACA{naca_code}', rey_number, alpha_start, alpha_end, alpha_inc,
                     job_dir, config.snapshot_settings() if settings is None else settings,
                     naca_code=naca_code)


def _execute_single_batch_run(args):
    code, rey, start, end, inc, job_dir, settings = args
    return _simulate(f'NACA{code}', rey, start, end, inc, job_dir, settings,
                     naca_code=code, cancel_event=_cancel_event)


def generate_batch_report(all_results, batch_output_dir, rey_number, alpha_start,
                           alpha_end, alpha_inc, settings=None):
    settings = config.snapshot_settings() if settings is None else settings
    ordered = sorted(all_results, key=lambda r: r['Airfoil'])
    valid = [r for r in ordered if r['success']]
    text = ('NACA AIRFOIL BATCH OPTIMIZATION REPORT\n\n'
            + analysis.settings_report(settings, rey_number, alpha_start, alpha_end, alpha_inc)
            + f'Total Airfoils: {len(ordered)}\nValid: {len(valid)}; Failed: {len(ordered)-len(valid)}\n'
            + '\n--- DETAILED RESULTS (SORTED BY NAME) ---\n'
            + 'Airfoil | Status | CL_max | CL/CD_max | CD_min\n')
    for result in ordered:
        text += f'{result["Airfoil"]} | {result["status"]} | '
        if result['success']:
            text += f'{result["CL_max"]:.4f} | {result["CL/CD_max"]:.3f} | {result["CD_min"]:.5f}\n'
        else:
            text += f'N/A | N/A | N/A - {result["error"]}\n'
        if 'requested_points' in result:
            text += (f'  Converged requested points: {result["converged_points"]}/{result["requested_points"]}; '
                     f'Missing angles: {result["missing_angles"] or "None"}\n')
        text += f'  Discarded polar rows: {result.get("discarded_rows", 0)}\n'
        if result.get('output_dir'):
            text += f'  Diagnostics: {result["output_dir"]}\n'
    text += '\n--- OPTIMAL AIRFOIL SUMMARY ---\n'
    if not valid:
        text += 'No valid candidates; all runs failed or no jobs were submitted.\n'
    else:
        for label, key, choose in [('Max Lift', 'CL_max', max), ('Max Eff', 'CL/CD_max', max),
                                   ('Min Drag', 'CD_min', min)]:
            winner = choose(valid, key=lambda r: r[key])
            text += f'{label}: {winner["Airfoil"]} ({key}: {winner[key]:.5f})\n'
        text += 'Rankings use converged sampled points; partial sweeps may miss extrema.\n'
    errors = analysis.plot_batch_results(ordered, batch_output_dir, rey_number, alpha_start, alpha_end, alpha_inc)
    text += f'\nArtifact delivery: {"incomplete" if errors else "complete"}\nArtifact errors: {errors or "None"}\n'
    path = Path(batch_output_dir) / 'Batch_Optimization_Report.txt'
    try:
        path.write_text(text, encoding='utf-8')
        print(f'Saved batch report: {path}')
    except OSError as exc:
        errors['Batch_Optimization_Report.txt'] = str(exc)
    if errors:
        print(f'Incomplete batch artifact delivery: {errors}')
    return errors


def run_xfoil_batch(naca_codes, rey_number, alpha_start, alpha_end, alpha_inc, max_workers=None):
    config.validate_simulation_inputs(rey_number, alpha_start, alpha_end, alpha_inc)
    codes = list(naca_codes)
    invalid = [code for code in codes if not config.valid_naca_code(code)]
    if invalid:
        raise ValueError(f'Invalid NACA codes: {invalid}')
    settings = config.snapshot_settings()
    batch_dir = Path(_setup_batch_environment(rey_number, alpha_start, alpha_end, alpha_inc))
    results = []
    context = multiprocessing.get_context('spawn')
    cancel = context.Event()
    with ProcessPoolExecutor(max_workers=max_workers, mp_context=context,
                             initializer=_init_worker, initargs=(cancel,)) as executor:
        jobs = {}
        try:
            for index, code in enumerate(codes):
                job_dir = batch_dir / f'{index+1:03d}_NACA{code}'
                args = (code, rey_number, alpha_start, alpha_end, alpha_inc, str(job_dir), settings)
                try:
                    jobs[executor.submit(_execute_single_batch_run, args)] = (code, job_dir)
                except Exception as exc:
                    results.append(_failed_result(f'NACA{code}', exc, settings, job_dir))
            for future in as_completed(jobs):
                code, job_dir = jobs[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(_failed_result(f'NACA{code}', exc, settings, job_dir))
        except KeyboardInterrupt:
            cancel.set()
            for future in jobs:
                future.cancel()
            raise
    for result in results:
        if not result['success']:
            job_dir = Path(result['output_dir'])
            job_dir.mkdir(parents=True, exist_ok=True)
            log_path = job_dir / 'xfoil.log'
            if not log_path.exists():
                log_path.write_text('Worker failure: ' + result['error'] + '\n', encoding='utf-8')
    artifact_errors = generate_batch_report(results, batch_dir, rey_number, alpha_start, alpha_end, alpha_inc, settings)
    for result in results:
        result['batch_artifact_errors'] = artifact_errors
        result.pop('polar_data', None)
    return results
