"""Read solver output and generate performance reports and plots."""
import os
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import config


def parse_polar_data(filename):
    """Return finite, positive-drag polar rows, or None for unusable output."""
    try:
        lines = Path(filename).read_text(encoding='utf-8', errors='replace').splitlines()
        # Locate the column separator rather than depending on a fixed header size.
        separator = next((i for i, line in enumerate(lines)
                          if line.strip().startswith('---') and i > 0
                          and 'alpha' in lines[i-1].lower()), None)
        rows = lines[separator + 1 if separator is not None else 12:]
        if not any(row.strip() for row in rows):
            return None
        valid, discarded = [], 0
        for row in rows:
            if not row.strip() or row.lstrip().startswith('#'):
                continue
            try:
                values = [float(v.replace('D', 'E').replace('d', 'e')) for v in row.split()]
                if len(values) < 5 or not np.isfinite([values[i] for i in (0, 1, 2, 4)]).all() or values[2] <= 0:
                    raise ValueError('Invalid polar row')
                if not np.isfinite(values[1] / values[2]):
                    raise ValueError('Invalid efficiency')
                valid.append(values[:5])
            except (ValueError, OverflowError):
                discarded += 1
        if not valid:
            return None
        data = np.array(valid)
        data = data[np.argsort(data[:, 0], kind='stable')]
        return {'alpha': data[:, 0], 'CL': data[:, 1], 'CD': data[:, 2],
                'CM': data[:, 4], 'CL/CD': data[:, 1] / data[:, 2], 'discarded_rows': discarded}
    except (OSError, ValueError, IndexError) as exc:
        print(f'Cannot read polar data: {exc}')
        return None


def read_coordinates(filename):
    """Read labeled or plain two-column XFOIL/Selig coordinates without losing a point."""
    lines = Path(filename).read_text(encoding='utf-8-sig').splitlines()
    lines = [line for line in lines if line.strip()]
    if not lines:
        raise ValueError('Coordinate file is empty.')
    try:
        first = [float(value) for value in lines[0].split()]
    except ValueError:
        first = []
    if len(first) != 2:
        lines = lines[1:]
    coords = np.loadtxt(lines, ndmin=2)
    if coords.shape[0] < 3 or coords.shape[1] != 2 or not np.isfinite(coords).all():
        raise ValueError('Expected at least three finite x/y coordinate pairs.')
    if np.unique(coords, axis=0).shape[0] < 3 or np.ptp(coords[:, 0]) <= 0:
        raise ValueError('Airfoil must have nonzero chord and at least three distinct points.')
    return coords


def calculate_airfoil_metrics(data, airfoil_name):
    if data is None or len(data['CL']) == 0:
        return {'Airfoil': airfoil_name, 'success': False, 'status': 'failed',
                'error': 'No converged polar data.', 'polar_data': None}
    alpha, cl, cd, efficiency = (data[k] for k in ('alpha', 'CL', 'CD', 'CL/CD'))
    max_lift, max_eff, min_drag = np.argmax(cl), np.argmax(efficiency), np.argmin(cd)
    linear = (alpha >= -5) & (alpha <= 5)
    slope = None
    reason = ('Unavailable: need distinct angles spanning at least 0.1 degrees and '
              'CL variation of at least 0.001 to resolve the polar rounding.')
    if (len(alpha[linear]) >= 2 and np.unique(alpha[linear]).size == len(alpha[linear])
            and np.ptp(alpha[linear]) >= 0.1 - 1e-10 and np.ptp(cl[linear]) >= 0.001 - 1e-10):
        try:
            slope = float(np.polyfit(np.deg2rad(alpha[linear]), cl[linear], 1)[0])
            if not np.isfinite(slope):
                slope = None
        except np.linalg.LinAlgError:
            pass
    return {
        'Airfoil': airfoil_name, 'success': True,
        'status': 'partial' if data.get('discarded_rows') else 'complete', 'error': '',
        'CL_max': float(cl[max_lift]), 'alpha_at_max_lift': float(alpha[max_lift]),
        # A sampled maximum alone does not establish aerodynamic stall.
        'alpha_stall': None,
        'CL/CD_max': float(efficiency[max_eff]), 'alpha_at_max_eff': float(alpha[max_eff]),
        'CL_at_max_eff': float(cl[max_eff]), 'CD_at_max_eff': float(cd[max_eff]),
        'CD_min': float(cd[min_drag]), 'alpha_at_min_drag': float(alpha[min_drag]),
        'CL_at_CD_min': float(cl[min_drag]), 'Lift_Slope_per_rad': slope,
        'Lift_Slope_reason': reason if slope is None else '',
        'discarded_rows': data.get('discarded_rows', 0), 'polar_data': data,
    }


def coverage(data, alpha_start, alpha_end, alpha_inc):
    requested = config.requested_angles(alpha_start, alpha_end, alpha_inc)
    actual = [] if data is None else list(data['alpha'])
    # Polar alpha is written to three decimal places by the bundled solver.
    missing = []
    for angle in requested:
        if actual:
            nearest = min(range(len(actual)), key=lambda i: abs(angle-actual[i]))
            if abs(angle-actual[nearest]) <= 0.00051:
                actual.pop(nearest)
                continue
        missing.append(angle)
    return {'requested_points': len(requested), 'converged_points': len(requested)-len(missing),
            'missing_angles': missing}


def settings_report(settings, rey_number, alpha_start, alpha_end, alpha_inc):
    return (f'Reynolds Number: {rey_number}\n'
            f'Alpha Range: {alpha_start} to {alpha_end} deg (Increment: {alpha_inc} deg)\n'
            f'Panel Nodes: {settings["panel_nodes"]}\n'
            f'Max Iterations: {settings["max_iterations"]}\n'
            f'Timeout: {settings["timeout_seconds"]} seconds\n')


def save_analysis_report_file(data, full_output_dir, airfoil_name, rey_number,
                              alpha_start, alpha_end, alpha_inc, *, settings=None, result=None):
    settings = config.snapshot_settings() if settings is None else settings
    metrics = calculate_airfoil_metrics(data, airfoil_name) if result is None else dict(result)
    metrics.update(coverage(data, alpha_start, alpha_end, alpha_inc))
    if metrics['success'] and (metrics['missing_angles'] or metrics.get('discarded_rows')):
        metrics['status'] = 'partial'
    text = ('XFOIL PERFORMANCE ANALYSIS REPORT\n'
            f'Airfoil: {airfoil_name}\nStatus: {metrics["status"]}\n\n'
            + settings_report(settings, rey_number, alpha_start, alpha_end, alpha_inc))
    text += (f'Converged requested points: {metrics["converged_points"]}/{metrics["requested_points"]}\n'
             f'Missing angles (degrees): {metrics["missing_angles"] or "None"}\n'
             f'Discarded polar rows: {metrics.get("discarded_rows", 0)}\n'
             'Reference: unit chord, leading edge at origin; custom geometry normalized by XFOIL.\n'
             'Moment reference: (0.25, 0); coordinate orientation is preserved.\n'
             f'Artifact delivery: {metrics.get("artifact_status", "not evaluated")}\n'
             f'Artifact errors: {metrics.get("artifact_errors") or "None"}\n\n')
    if not metrics['success']:
        text += f'--- ANALYSIS FAILED ---\n{metrics["error"]}\nSee xfoil.log for solver diagnostics.\n'
    else:
        slope = metrics['Lift_Slope_per_rad']
        slope_text = (metrics.get('Lift_Slope_reason', 'Unavailable (insufficient resolved data).')
                      if slope is None else f'{slope:.4f} per radian (or {slope*np.pi/180:.4f} per degree)')
        text += (f'1. MAXIMUM OBSERVED LIFT\n'
                 f'   Max Lift Coefficient (C_L_max): {metrics["CL_max"]:.4f}\n'
                 f'   Angle of maximum observed lift: {metrics["alpha_at_max_lift"]:.2f} degrees\n'
                 '   Stall angle: Undetermined; a sampled maximum does not establish stall.\n\n'
                 f'2. MAXIMUM EFFICIENCY\n'
                 f'   Max Lift-to-Drag Ratio: {metrics["CL/CD_max"]:.3f}\n'
                 f'   Angle: {metrics["alpha_at_max_eff"]:.2f} degrees\n'
                 f'   CL: {metrics["CL_at_max_eff"]:.4f}; CD: {metrics["CD_at_max_eff"]:.5f}\n\n'
                 f'3. MINIMUM DRAG\n'
                 f'   CD_min: {metrics["CD_min"]:.5f}\n'
                 f'   Angle: {metrics["alpha_at_min_drag"]:.2f} degrees\n'
                 f'   CL: {metrics["CL_at_CD_min"]:.4f}\n\n'
                 f'4. LIFT SLOPE\n   {slope_text}\n'
                 '   Least-squares fit over available angles from -5 to 5 degrees.\n\n'
                 'Results describe converged sampled points only; partial sweeps may miss extrema.\n')
    path = Path(full_output_dir) / 'Analysis_Report.txt'
    path.write_text(text, encoding='utf-8')
    print(f'Saved Analysis Report to: {path}')
    metrics.pop('polar_data', None)
    return metrics

# --- Plotting Functions ---

def _save_plot(directory, filename, draw):
    before = set(plt.get_fignums())
    try:
        figure = draw()
        figure.savefig(Path(directory) / filename, bbox_inches='tight')
        return {}
    except Exception as exc:
        return {filename: str(exc)}
    finally:
        for number in set(plt.get_fignums()) - before:
            plt.close(number)


def plot_airfoil_shape(coords_file, full_output_dir, airfoil_name):
    def draw():
        coords = read_coordinates(coords_file)
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.plot(coords[:, 0], coords[:, 1], '.-', markersize=2, linewidth=.5, color='black')
        ax.set_aspect('equal', adjustable='box')
        ax.set(title=f'Airfoil Geometry: {airfoil_name}', xlabel='X/c', ylabel='Y/c')
        ax.grid(True, linestyle='--', alpha=.5)
        return fig
    return _save_plot(full_output_dir, 'Airfoil_Shape.png', draw)


def plot_results(data, full_output_dir, subfolder_name):
    if data is None:
        return {}
    def lift():
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(data['alpha'], data['CL'], 'o-', color='tab:blue')
        ax.set(xlabel='Angle of Attack (degrees)', ylabel='Lift Coefficient CL',
               title=f'Lift and Efficiency Polar for {subfolder_name}')
        right = ax.twinx()
        right.plot(data['alpha'], data['CL/CD'], 'x--', color='tab:red')
        right.set_ylabel('CL/CD Ratio (Efficiency)')
        ax.grid(True, linestyle='--', alpha=.6)
        return fig
    def drag():
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.plot(data['CD'], data['CL'], 'o-', color='tab:green')
        ax.set(xlabel='Drag Coefficient CD', ylabel='Lift Coefficient CL',
               title=f'Lift-Drag Polar for {subfolder_name}')
        ax.grid(True, linestyle='--', alpha=.6)
        return fig
    return {**_save_plot(full_output_dir, 'CL_vs_Alpha.png', lift),
            **_save_plot(full_output_dir, 'CL_vs_CD_Polar.png', drag)}


def plot_batch_results(all_results, batch_output_dir, rey_number, alpha_start, alpha_end, alpha_inc):
    valid = [r for r in all_results if r.get('success') and r.get('polar_data')]
    if not valid:
        return {}
    def draw(key, ylabel):
        fig, ax = plt.subplots(figsize=(10, 7))
        for result in valid:
            data = result['polar_data']
            ax.plot(data['alpha'], data[key], label=result['Airfoil'])
        ax.set(xlabel='Angle of Attack (degrees)', ylabel=ylabel,
               title=f'Batch {ylabel} Comparison (Re: {rey_number})')
        ax.legend(loc='best', frameon=True)
        ax.grid(True, linestyle='--', alpha=.6)
        return fig
    return {**_save_plot(batch_output_dir, 'Batch_CL_vs_Alpha_Comparison.png', lambda: draw('CL', 'CL')),
            **_save_plot(batch_output_dir, 'Batch_Efficiency_Comparison.png', lambda: draw('CL/CD', 'CL/CD'))}
