"""模擬サーバーの保存先をOSの一時領域へ限定する。"""
import os
from pathlib import Path
import tempfile


def temporary_path(value):
    path = Path(value).resolve()
    roots = {Path(tempfile.gettempdir()).resolve(), Path('/tmp').resolve()}
    if not any(path != root and path.is_relative_to(root) for root in roots):
        raise ValueError(f'test storage must be inside a temporary directory: {path}')
    return path


def isolated_storage(directory, settings, extra_args):
    """起動前に全保存先を検査し、本番の保存設定を子プロセスへ引き継がない。"""
    directory = temporary_path(directory)
    for arg in extra_args:
        if arg.split('=', 1)[0] in {'--haiku-settings', '--audio-dir'}:
            raise ValueError('test storage must be configured through the fixture')
    settings = dict(settings or {})
    for key, name in [('memory_dir', 'memory'), ('screenshot_dir', 'screenshots'),
                      ('capture_dir', 'captures')]:
        settings[key] = str(temporary_path(settings.get(key) or directory / name))
    settings.setdefault('internal_capture_enabled', False)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('DOGIDO_')}
    # HOMEも隔離し、本棚の既定の写真庫を個人のPicturesへ向けない。
    env['HOME'] = str(directory / 'home')
    env['DOGIDO_EPISODE_MEMORY_DIR'] = str(directory / 'episodes')
    return settings, env
