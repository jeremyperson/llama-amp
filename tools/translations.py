#!/usr/bin/env python3
"""Maintain translations (GNU gettext, domain "llamaamp").

    python3 tools/translations.py extract      # refresh po/llamaamp.pot from the code
    python3 tools/translations.py compile      # po/*.po -> locale/<lang>/LC_MESSAGES/llamaamp.mo
    python3 tools/translations.py compile DIR  # ... into DIR/<lang>/LC_MESSAGES (packaging)

A running checkout picks up compiled catalogs from ./locale automatically.
Needs xgettext/msgfmt (Debian/Ubuntu: the gettext package).
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PO = ROOT / 'po'
DOMAIN = 'llamaamp'


def extract():
    sources = sorted(str(path.relative_to(ROOT)) for path in (ROOT / 'llamaamp').rglob('*.py'))
    version = next(line.split('"')[1] for line in (ROOT / 'llamaamp' / 'constants.py').read_text().splitlines()
                   if line.startswith('APP_VERSION'))
    subprocess.run(['xgettext', '--language=Python', '--from-code=UTF-8', '--keyword=_', '--keyword=N_',
                    '--keyword=ngettext:1,2', '--add-comments=Translators:', '--sort-by-file',
                    '--package-name=Llama Amp', f'--package-version={version}',
                    '--msgid-bugs-address=https://github.com/jeremyperson/llama-amp/issues',
                    '-o', str(PO / f'{DOMAIN}.pot'), *sources], cwd=ROOT, check=True)
    for catalog in sorted(PO.glob('*.po')):
        subprocess.run(['msgmerge', '--quiet', '--update', '--backup=none', str(catalog),
                        str(PO / f'{DOMAIN}.pot')], check=True)
    print(f'{PO / f"{DOMAIN}.pot"} updated; merged {len(list(PO.glob("*.po")))} catalog(s)')


def compile_catalogs(target):
    catalogs = sorted(PO.glob('*.po'))
    for catalog in catalogs:
        out = target / catalog.stem / 'LC_MESSAGES' / f'{DOMAIN}.mo'
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['msgfmt', '--check', '-o', str(out), str(catalog)], check=True)
    print(f'compiled {len(catalogs)} catalog(s) into {target}')


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    if command == 'extract':
        extract()
    elif command == 'compile':
        compile_catalogs(Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / 'locale')
    else:
        sys.exit(__doc__)
