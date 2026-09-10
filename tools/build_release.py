"""Build a deterministic source ZIP using an explicit, reviewed file manifest."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def release_files():
    names = [line.strip() for line in (ROOT/'release_files.txt').read_text().splitlines()
             if line.strip() and not line.startswith('#')]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate entries in release manifest.')
    for name in names:
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or (ROOT/name).is_symlink():
            raise ValueError(f'Invalid release path: {name}')
        if path.suffix.lower() in ('.exe', '.zip', '.pyc') or name.startswith(('.', 'output/')) and name not in (
                '.gitignore', '.gitattributes', '.github/workflows/tests.yml'):
            raise ValueError(f'Unexpected release content: {name}')
    return names


def build():
    destination = ROOT/'dist'/'airfoil-optimizer-source.zip'
    destination.parent.mkdir(exist_ok=True)
    names = release_files()
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            info = zipfile.ZipInfo('airfoil-optimizer/' + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            # Normalize text so the ZIP matches a Git checkout on any platform.
            if Path(name).suffix.lower() == '.png':
                content = (ROOT/name).read_bytes()
            else:
                content = (ROOT/name).read_text(encoding='utf-8-sig').replace('\r\n', '\n').encode('utf-8')
            archive.writestr(info, content)
    print(f'Built {destination.name}: {len(names)} source files, {destination.stat().st_size} bytes.')
    return destination


if __name__ == '__main__':
    build()
