#!/usr/bin/env python3
"""Regenerate tests/golden/*.png after an intended visual change:

    xvfb-run -a python3 tools/update_golden.py

Look at every changed image before committing it."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

os.environ['LLAMAAMP_DATA_DIR'] = tempfile.mkdtemp(prefix='llama-golden-')
Path(os.environ['LLAMAAMP_DATA_DIR'], 'config.json').write_text(json.dumps(
    {'alsa_output': False, 'direct_mode': False, 'tray_icon': False, 'notifications': False,
     'update_check': False}))

from golden_scenes import GOLDEN_DIR, SCENES, difference, render  # noqa: E402
from llamaamp.app import MusicPlayer  # noqa: E402

with patch.object(MusicPlayer, '_mpris_setup', lambda self: None):
    app = MusicPlayer()
app.show_all()
app.set_skin('builtin')
GOLDEN_DIR.mkdir(exist_ok=True)
for scene in SCENES:
    surface = render(app, scene)
    path = GOLDEN_DIR / f'{scene}.png'
    changed = difference(surface, path)
    if changed:
        surface.write_to_png(str(path))
    print(f'{scene}: {"unchanged" if not changed else f"written ({changed:.1%} of pixels differed)"}')
app.destroy()
