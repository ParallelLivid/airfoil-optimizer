"""Simulation settings shared by the CLI and workers."""
import json
import math
import os
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE_PATH = PROJECT_DIR / 'xfoil_config.json'
OUTPUT_DIR = PROJECT_DIR / 'output'
DEFAULT_SETTINGS = {'max_iterations': 100, 'panel_nodes': 160, 'timeout_seconds': 120.0}
MAX_ITERATIONS = DEFAULT_SETTINGS['max_iterations']
PANEL_NODES = DEFAULT_SETTINGS['panel_nodes']
TIMEOUT_SECONDS = DEFAULT_SETTINGS['timeout_seconds']
# Bundled XFOIL.INC: IQX=500; xfoil.f caps NPAN at IQX-6.
MIN_PANEL_NODES = 10
MAX_PANEL_NODES = 494
MAX_REYNOLDS = 10**12  # Conservative interface limit, not an aerodynamic validity claim.
MAX_LOG_BYTES = 4 * 1024 * 1024
MIN_ALPHA_INCREMENT = 0.001


def validate_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError('Settings must be a JSON object.')
    result = {**DEFAULT_SETTINGS, **settings}
    iterations, panels, timeout = (result[k] for k in DEFAULT_SETTINGS)
    if type(iterations) is not int or not 0 < iterations <= 2147483647:
        raise ValueError('Max iterations must be a positive 32-bit integer.')
    if type(panels) is not int or panels % 2 or not MIN_PANEL_NODES <= panels <= MAX_PANEL_NODES:
        raise ValueError(f'Panel nodes must be an even integer from {MIN_PANEL_NODES} to {MAX_PANEL_NODES}.')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 86400:
        raise ValueError('Timeout must be finite and greater than 0, up to 86400 seconds.')
    return {key: result[key] for key in DEFAULT_SETTINGS}


def snapshot_settings():
    return validate_settings({'max_iterations': MAX_ITERATIONS, 'panel_nodes': PANEL_NODES,
                              'timeout_seconds': TIMEOUT_SECONDS})


def load_settings():
    """Apply a validated snapshot atomically; invalid files restore all defaults."""
    global MAX_ITERATIONS, PANEL_NODES, TIMEOUT_SECONDS
    try:
        with open(SETTINGS_FILE_PATH, encoding='utf-8-sig') as stream:
            settings = validate_settings(json.load(stream))
        print(f'Loaded settings from {SETTINGS_FILE_PATH}.')
    except (OSError, ValueError, TypeError) as exc:
        settings = DEFAULT_SETTINGS.copy()
        print(f'Using default settings: {exc}')
    MAX_ITERATIONS = settings['max_iterations']
    PANEL_NODES = settings['panel_nodes']
    TIMEOUT_SECONDS = settings['timeout_seconds']


def save_settings():
    temporary = None
    try:
        settings = snapshot_settings()
        destination = Path(SETTINGS_FILE_PATH).resolve()
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         prefix=destination.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(settings, stream, indent=4)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        print(f'Settings saved to {SETTINGS_FILE_PATH}.')
        return True
    except (OSError, ValueError) as exc:
        print(f'Error saving settings: {exc}')
        return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_simulation_inputs(rey_number, alpha_start, alpha_end, alpha_inc):
    if type(rey_number) is not int or not 0 < rey_number <= MAX_REYNOLDS:
        raise ValueError(f'Reynolds number must be an integer from 1 to {MAX_REYNOLDS}.')
    try:
        finite = all(math.isfinite(v) for v in (rey_number, alpha_start, alpha_end, alpha_inc))
    except (OverflowError, TypeError):
        finite = False
    if not finite:
        raise ValueError('Simulation inputs must be finite numbers.')
    if not all(-180 <= a <= 180 for a in (alpha_start, alpha_end)):
        raise ValueError('Angles must be between -180 and 180 degrees (interface limits).')
    if not MIN_ALPHA_INCREMENT <= alpha_inc <= 360:
        raise ValueError('Alpha increment must be between 0.001 and 360 degrees (interface limits).')
    if any(not math.isclose(a * 1000, round(a * 1000), abs_tol=1e-7, rel_tol=0)
           for a in (alpha_start, alpha_end, alpha_inc)):
        raise ValueError('Angles and increment must be multiples of 0.001 degrees.')
    if alpha_inc <= 0 or alpha_start >= alpha_end:
        raise ValueError('Alpha increment must be positive and start must be less than end.')
    # Keep sequence planning bounded; XFOIL stores at most NAX=800 polar points.
    span = (alpha_end - alpha_start) / alpha_inc
    if not math.isfinite(span) or span > 799:
        raise ValueError('Request at most 800 angles per sweep.')


def requested_angles(alpha_start, alpha_end, alpha_inc):
    # Match the ASEQ rounding rule in bundled xoper.f.
    count = int((alpha_end - alpha_start) / alpha_inc + 0.5) + 1
    return [alpha_start + i * alpha_inc for i in range(count)]


def valid_naca_code(code):
    return isinstance(code, str) and len(code) == 4 and code.isascii() and code.isdigit()


def get_filenames(full_output_dir):
    return (os.path.join(full_output_dir, 'polar.txt'),
            os.path.join(full_output_dir, 'coordinates.dat'))


def _format_rey_alpha(rey_number, alpha_start, alpha_end, alpha_inc):
    return f'Re{rey_number:.6g}', f'A{alpha_start:.6g}-{alpha_end:.6g}inc{alpha_inc:.6g}'
