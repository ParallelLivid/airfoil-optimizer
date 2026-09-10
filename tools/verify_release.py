"""Install the source archive into a fresh temporary environment and run its tests."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

from build_release import ROOT, build


def verify(solver=None):
    source_zip = build()
    if solver is not None:
        solver = Path(solver).resolve(strict=True)
        if not solver.is_file():
            raise ValueError('Solver path must be a file.')
    with tempfile.TemporaryDirectory(prefix='release_check_', dir=ROOT) as temp:
        location = Path(temp).resolve()
        if not location.is_relative_to(ROOT):
            raise ValueError('Temporary verification directory escaped the project.')
        with zipfile.ZipFile(source_zip) as archive:
            for item in archive.namelist():
                if not (location/item).resolve().is_relative_to(location):
                    raise ValueError('Unsafe archive entry.')
            archive.extractall(location)
        source = location/'airfoil-optimizer'
        environment = location/'venv'
        subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True, timeout=60)
        python = environment/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(source/'requirements.txt')],
                       check=True, timeout=180)
        env = dict(os.environ, XFOIL_SMOKE='1' if solver else '0')
        if solver:
            # Test-only copy. The manifest and ZIP never include this external binary.
            shutil.copy2(solver, source/('xfoil.exe' if os.name == 'nt' else 'xfoil'))
        subprocess.run([str(python), '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                       cwd=source, env=env, check=True, timeout=90)
        print('Fresh source release passed ' + ('regression and real-solver tests.' if solver else
                                               'regression tests (real-solver checks not requested).'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--solver', help='Optional local XFOIL executable for real integration checks.')
    verify(parser.parse_args().solver)
