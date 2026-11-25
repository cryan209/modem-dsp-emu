"""The launcher must not accidentally use macOS's old system Python."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_launcher_uses_project_python_without_activation(tmp_path):
    # No local profile overrides, captures, or dependency on the real .venv.
    (tmp_path / 'tools').mkdir()
    (tmp_path / '.venv/bin').mkdir(parents=True)
    for name in ('run', 'profiles.toml', 'tools/run.py'):
        shutil.copy2(ROOT / name, tmp_path / name)
    (tmp_path / '.venv/bin/python').symlink_to(sys.executable)
    result = subprocess.run(
        [str(tmp_path / 'run'), '-n', 'native-tower', '--run', '1'],
        cwd=tmp_path.parent, env={**os.environ, 'PATH': '/usr/bin:/bin'},
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert '.venv/bin/python' in result.stderr
    assert '/tmp/eicon' not in result.stderr
