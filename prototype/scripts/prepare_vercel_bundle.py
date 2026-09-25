"""Build an allowlisted deployment directory without local secrets or trial data."""
import json
from pathlib import Path
import shutil

BASE = Path(__file__).resolve().parents[1]
DEST = BASE / '.vercel-package'


def main():
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(mode=0o700)
    files = [*BASE.joinpath('app').glob('*.py'),
             *[p for p in BASE.joinpath('web').rglob('*') if p.is_file()],
             *[BASE / p for p in (
                 'deployment/data/catalog-runtime.json.gz',
                 'deployment/data/catalog-runtime.manifest.json',
                 'data/catalog-quality-overrides.json',
                 'data/CATALOG-SOURCES.md', 'data/METADATA-EVIDENCE.md',
                 'requirements.txt', 'pyproject.toml', 'vercel.json',
                 '.vercel/project.json')]]
    for source in files:
        relative = source.relative_to(BASE)
        destination = DEST / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    manifest = {'files': sorted(str(p.relative_to(BASE)) for p in files),
                'bytes': sum(p.stat().st_size for p in files),
                'source_feedback_included': False, 'credentials_included': False}
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
