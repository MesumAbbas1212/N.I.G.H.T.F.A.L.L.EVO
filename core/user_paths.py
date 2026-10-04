import os
from pathlib import Path

def get_user_data_dir() -> Path:
    app_data = os.getenv('LOCALAPPDATA', os.path.expanduser('~'))
    d = Path(app_data) / 'NIGHTFALLAI'
    d.mkdir(parents=True, exist_ok=True)
    return d
