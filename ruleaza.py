"""Rulează etape stabile din analiza.ipynb, în același mediu Python."""
from pathlib import Path
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import os
import sys
import time
import traceback

import nbformat


STEPS = {
    'inventar': ['pregatire', 'inventar'],
    'geometrie': ['pregatire', 'contur', 'geometrie'],
    'pixeli': ['pregatire', 'functii', 'pixeli'],
    'grila': ['pregatire', 'functii', 'grila'],
    'aliniere': ['pregatire', 'functii', 'grila', 'aliniere'],
    'harti': ['pregatire', 'harti'],
    'diagnostic': ['pregatire', 'diagnostic'],
}


class Tee(io.StringIO):
    def __init__(self, terminal):
        super().__init__()
        self.terminal = terminal

    def write(self, value):
        self.terminal.write(value)
        return super().write(value)

    def flush(self):
        self.terminal.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('etapa', choices=['toate', *STEPS])
    parser.add_argument('--scanare-integrala', action='store_true',
                        help='Citește integral rasterele la inventariere; implicit doar metadate.')
    args = parser.parse_args()
    if args.scanare_integrala and args.etapa not in ('inventar', 'toate'):
        parser.error('--scanare-integrala se aplică numai etapelor inventar/toate.')
    root = Path(__file__).resolve().parent
    os.chdir(root)
    os.environ['MEDE_FULL_SCAN'] = '1' if args.scanare_integrala else '0'
    path = root / 'analiza.ipynb'
    notebook = nbformat.read(path, as_version=4)
    cells = {}
    for cell in notebook.cells:
        if cell.cell_type == 'code':
            name = cell.metadata.get('etapa')
            if not name or name in cells:
                raise ValueError('Fiecare celulă de cod trebuie să aibă o etapă unică.')
            cells[name] = cell
    names = list(cells) if args.etapa == 'toate' else STEPS[args.etapa]
    scope = {'__name__': '__main__'}
    provenance = None
    start = time.perf_counter()
    for count, name in enumerate(names, 1):
        cell = cells[name]
        print(f'\n--- {name} ---', flush=True)
        stdout, stderr = Tee(sys.stdout), Tee(sys.stderr)
        failure = None
        cell_start = time.perf_counter()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exec(compile(cell.source, f'analiza.ipynb:{name}', 'exec'), scope)
        except Exception as error:
            failure = error
        cell.execution_count = count
        cell.outputs = []
        for stream, capture in [('stdout', stdout), ('stderr', stderr)]:
            if capture.getvalue():
                cell.outputs.append(nbformat.v4.new_output(
                    'stream', name=stream, text=capture.getvalue()))
        cell.metadata['executat_utc'] = datetime.now(timezone.utc).isoformat()
        cell.metadata['durata_secunde'] = round(time.perf_counter() - cell_start, 3)
        if failure is None and name == 'pregatire':
            inputs = [file for _, file in scope['files']]
            inputs += list(root.glob('date/landsat/*_MTL.txt'))
            inputs += list(root.glob('date/sentinel2/*.SAFE/MTD_MSIL2A.xml'))
            inputs.append(root / 'date/bucuresti.geojson')
            provenance = {
                'surse': {str(file.relative_to(root)): {
                    'bytes': file.stat().st_size, 'mtime_ns': file.stat().st_mtime_ns
                } for file in inputs},
                'cod_sha256': hashlib.sha256('\n'.join(
                    cells[key].source for key in ['pregatire', 'functii', 'grila', 'aliniere']
                ).encode()).hexdigest(),
            }
            previous = notebook.metadata.get('mede_aliniere', {})
            if args.etapa in ('harti', 'diagnostic') and previous:
                if any(previous.get(key) != value for key, value in provenance.items()):
                    failure = ValueError('Sursele sau codul de aliniere s-au schimbat. '
                                         'Rulează întâi: ruleaza.py aliniere')
        if failure is None and name == 'aliniere':
            notebook.metadata['mede_aliniere'] = {
                **provenance,
                'executat_utc': datetime.now(timezone.utc).isoformat(),
                'celule': len(scope['aligned']),
            }
        if failure is not None:
            cell.outputs.append(nbformat.v4.new_output(
                'error', ename=type(failure).__name__, evalue=str(failure),
                traceback=traceback.format_exception(failure)))
        nbformat.write(notebook, path)
        if failure is not None:
            raise failure
    print(f'\nEtapa {args.etapa}: finalizată în {time.perf_counter()-start:.1f} s. '
          'Rezultatele celulelor sunt salvate în notebook.')


if __name__ == '__main__':
    main()
