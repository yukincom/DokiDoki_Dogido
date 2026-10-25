#!/usr/bin/env python3
"""検証済みの本棚書出しだけを固定の非公開repositoryへ送る。LLM・採取・原本削除はしない。"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess
import tempfile

REPOSITORY = 'yukincom/DokiDoki-Dogido-data'
URL = f'https://github.com/{REPOSITORY}'
REMOTE = URL + '.git'
README = '''# ドギドの研究データ

句集・J-lens候補・採取した内部表現を同じ対応表で保管する非公開リポジトリです。

- `index.json`: 句ID、保存時点のsnapshot、要求順、採取記録、原配列、J-lens結果の対応とSHA-256。
- `年/月日時分/snapshots/`: 元句・現在句・改訂・対話・材料・メモ。元の保存形式とhashを維持します。
- `年/月日時分/internal-representations/`: 検証済みの `manifest.json`、`hidden.f32`、`commit.json`。原配列はGit LFS。
- `年/月日時分/j-lens/`: 前・中・後、全30層、各16候補。手動翻訳がある場合は `translations/` に別保存。

写真は参照情報のみです。モデル本体・J-lens重み・認証情報は含みません。採取開始前の句には内部表現がありません。
途中層のJ-lensは既存レンズの試験的転用です。訳文は補助表示で、原文・スコアとは別に保持します。

本体と操作説明: [DokiDoki_Dogido](https://github.com/yukincom/DokiDoki_Dogido)

取得にはこの非公開リポジトリへの権限とGit LFSが必要です。通常のclone後に `git lfs pull` を実行し、`index.json` のhashと照合してください。
アップロードは本棚のボタンを押したときだけ実行します。古いsnapshotは上書きせず、最新版への対応だけ更新します。Mac・SSDの原本は削除しません。
'''


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_path(root, relative):
    p = PurePosixPath(relative)
    if not relative or p.is_absolute() or any(x in ('', '.', '..', '.git') for x in relative.split('/')) or '\\' in relative or '\0' in relative:
        raise ValueError('保管ファイルのパスが不正です。')
    target = root
    for part in p.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError('保管先にシンボリックリンクがあります。')
    return target


def run(args, *, cwd=None, timeout=120, env=None, input=None, check=True):
    executable_path = os.pathsep.join([p for p in ['/opt/homebrew/bin', '/usr/local/bin', os.environ.get('PATH', '')] if p])
    try:
        result = subprocess.run(args, cwd=cwd, timeout=timeout, input=input, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env={**os.environ, 'PATH': executable_path, 'GIT_TERMINAL_PROMPT': '0', **(env or {})})
    except subprocess.TimeoutExpired:
        if os.getpgrp() == os.getpid():
            print(json.dumps({'status': 'failed', 'detail': 'アップロードが制限時間を超えたため停止しました。再度ボタンを押すと続きから再送できます。'}, ensure_ascii=False), flush=True)
            os.killpg(os.getpid(), signal.SIGKILL)
        raise
    if check and result.returncode:
        # 認証値やリモートが付加した任意の文字列を本棚・ログへ転送しない。
        raise RuntimeError(f'{args[0]} の処理に失敗しました。接続・GitHub認証・LFS容量を確認してください。')
    return result


def git(repo, *args, **kwargs):
    return run(['git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
                '-c', f'lfs.url={REMOTE}/info/lfs', '-c', f'lfs.pushurl={REMOTE}/info/lfs',
                '-c', f'remote.origin.lfsurl={REMOTE}/info/lfs', '-c', f'remote.origin.lfspushurl={REMOTE}/info/lfs',
                '-C', str(repo), *args], **kwargs)


def check_destination(root):
    for option in [[], ['--push']]:
        if git(root, 'remote', 'get-url', *option, '--all', 'origin').stdout.decode().splitlines() != [REMOTE]:
            raise ValueError('リモートの保存先が変更されています。')
    if (root / '.lfsconfig').exists() or (root / '.lfsconfig').is_symlink():
        raise ValueError('LFSの送信先が上書きされています。')


def check_files(root, files):
    for file in files:
        path = safe_path(root, file['path'])
        if not path.is_file() or path.stat().st_size != file['bytes'] or digest(path) != file['sha256']:
            raise ValueError('保存後のファイル照合に失敗しました: ' + file['path'])


def write_file(root, file):
    target = safe_path(root, file['path'])
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == file['bytes'] and digest(target) == file['sha256']:
        return
    if target.exists() and file['path'] not in ('index.json', 'README.md', '.gitattributes', '.gitignore'):
        raise ValueError('既存の研究記録を上書きしようとしました: ' + file['path'])
    staging = root / '.git/dogido-stage' if (root / '.git').is_dir() else target.parent
    if staging.is_symlink():
        raise ValueError('一時保存先を確認できません。')
    staging.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=staging)
    try:
        with os.fdopen(fd, 'wb') as dest:
            if 'content' in file:
                dest.write(file['content'].encode('utf-8'))
            else:
                source = Path(file['source'])
                if not source.is_absolute() or any(p.is_symlink() for p in [source, *source.parents]):
                    raise ValueError('保管元のパスを確認できません。')
                with source.open('rb') as src:
                    shutil.copyfileobj(src, dest, 1024 * 1024)
            dest.flush()
            os.fsync(dest.fileno())
        if Path(temporary).stat().st_size != file['bytes'] or digest(temporary) != file['sha256']:
            raise ValueError('コピー中に研究記録が変わりました: ' + file['path'])
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def text_file(path, text):
    encoded = text.encode('utf-8')
    return {'path': path, 'content': text, 'bytes': len(encoded), 'sha256': hashlib.sha256(encoded).hexdigest()}


def journal(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.journal-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f); f.flush(); os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def changed_records(plan, previous):
    index = next(f for f in plan['files'] if f['path'] == 'index.json')
    current = json.loads(index['content'])
    old = {row['id']: row for row in previous.get('poems', [])}
    changed = [row for row in current['poems'] if row != old.get(row['id'])]
    return len(changed), sum('hidden' in request for row in changed for request in row['requests'])


def publish(plan):
    if plan['repository'] != REPOSITORY:
        raise ValueError('保存先が指定されたリポジトリではありません。')
    remote = json.loads(run(['gh', 'api', f'repos/{REPOSITORY}'], timeout=30).stdout)
    if remote.get('private') is not True or remote.get('full_name') != REPOSITORY:
        raise ValueError('保存先が非公開ではありません。アップロードを中止しました。')
    root = Path(plan['checkout'])
    if not root.is_absolute() or any(p.is_symlink() for p in [root, *root.parents]):
        raise ValueError('データ用の作業フォルダを確認できません。')
    root.mkdir(parents=True, exist_ok=True)
    if not (root / '.git').exists():
        if any(root.iterdir()):
            raise ValueError('データ用の作業フォルダに別のファイルがあります。')
        git(root, 'init', '-b', 'main')
        git(root, 'remote', 'add', 'origin', REMOTE)
    if not (root / '.git').is_dir() or (root / '.git').is_symlink():
        raise ValueError('独立したデータ用リポジトリではありません。')
    with (root / '.git/dogido-publish.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('別のアップロードが進行中です。')
        check_destination(root)
        if git(root, 'branch', '--show-current').stdout.decode().strip() != 'main' or (root / '.lfsconfig').exists():
            raise ValueError('データ用リポジトリの設定が想定と異なります。')
        working_path = root / '.git/dogido-publish-working.json'
        if working_path.is_symlink():
            raise ValueError('アップロード準備記録を確認できません。')
        working = json.loads(working_path.read_text()) if working_path.exists() else {}
        dirty = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all').stdout.decode().split('\0')
        for status in filter(None, dirty):
            path = status[3:]
            expected = working.get('files', {}).get(path)
            target = safe_path(root, path)
            if not expected or not target.is_file() or digest(target) != expected:
                raise ValueError('データ用作業フォルダに処理外の変更があります。変更を確認してください。')
        git(root, 'lfs', 'install', '--local')
        heads = git(root, 'ls-remote', '--heads', 'origin', 'main').stdout.strip()
        remote_head = heads.decode().split()[0] if heads else None
        head_result = git(root, 'rev-parse', '--verify', 'HEAD', check=False)
        head = head_result.stdout.decode().strip() if head_result.returncode == 0 else None
        pending_path = root / '.git/dogido-publish-pending.json'
        if pending_path.is_symlink():
            raise ValueError('アップロード待ち記録を確認できません。')
        pending = json.loads(pending_path.read_text()) if pending_path.exists() else {}
        if heads:
            git(root, 'fetch', '--quiet', 'origin', 'main', env={'GIT_LFS_SKIP_SMUDGE': '1'})
        previous = json.loads(git(root, 'show', 'FETCH_HEAD:index.json').stdout) if heads else {}
        changed_poems, changed_captures = changed_records(plan, previous)
        if head and head != remote_head:
            behind = heads and git(root, 'merge-base', '--is-ancestor', head, 'FETCH_HEAD', check=False).returncode == 0
            head_tree = git(root, 'rev-parse', 'HEAD^{tree}').stdout.decode().strip()
            parents = git(root, 'rev-list', '--parents', '-n', '1', 'HEAD').stdout.decode().split()[1:]
            expected_parents = [working['commit_parent']] if working.get('commit_parent') else []
            own_retry = (pending.get('commit') == head and pending.get('remote_base') == remote_head
                         or working.get('tree') == head_tree and working.get('remote_base') == remote_head and parents == expected_parents)
            if not behind and not own_retry:
                raise ValueError('アップロード処理以外の未送信コミットがあります。自動では送信しません。')
        if heads:
            if git(root, 'rev-parse', '--verify', 'HEAD', check=False).returncode == 0:
                git(root, 'merge', '--ff-only', 'FETCH_HEAD', env={'GIT_LFS_SKIP_SMUDGE': '1'})
            else:
                git(root, 'reset', '--soft', 'FETCH_HEAD')
                git(root, 'read-tree', '--reset', '-u', 'HEAD', env={'GIT_LFS_SKIP_SMUDGE': '1'})
            check_destination(root)
            git(root, 'lfs', 'pull', timeout=900)
        files = list(plan['files']) + [text_file('README.md', README), text_file('.gitattributes', '*.f32 filter=lfs diff=lfs merge=lfs -text\n'), text_file('.gitignore', '.DS_Store\n')]
        paths = [f['path'] for f in files]
        if len(paths) != len(set(paths)):
            raise ValueError('同じパスの記録が重複しています。')
        for file in files:
            safe_path(root, file['path'])
            if len(file['sha256']) != 64 or not all(c in '0123456789abcdef' for c in file['sha256']):
                raise ValueError('記録のhashが不正です。')
        working = {'remote_base': remote_head, 'files': {f['path']: f['sha256'] for f in files}}
        journal(working_path, working)
        for file in files:
            write_file(root, file)
        check_files(root, files)
        git(root, 'add', '--', *paths)
        working['tree'] = git(root, 'write-tree').stdout.decode().strip()
        parent = git(root, 'rev-parse', '--verify', 'HEAD', check=False)
        working['commit_parent'] = parent.stdout.decode().strip() if parent.returncode == 0 else None
        journal(working_path, working)
        changed = git(root, 'diff', '--cached', '--quiet', check=False).returncode != 0
        if not changed and working['commit_parent'] == remote_head and not pending:
            working_path.unlink()
            return {'status': 'up_to_date', 'url': URL, 'commit': working['commit_parent'], 'poems': 0, 'captures': 0}
        if changed:
            git(root, 'commit', '-m', '句集とJ-lens候補および内部表現を保管')
        commit = git(root, 'rev-parse', 'HEAD').stdout.decode().strip()
        journal(pending_path, {'commit': commit, 'remote_base': remote_head})
        git(root, 'lfs', 'fsck', timeout=300)
        if json.loads(run(['gh', 'api', f'repos/{REPOSITORY}'], timeout=30).stdout).get('private') is not True:
            raise ValueError('保存先が非公開ではなくなったため、送信を中止しました。')
        check_destination(root)
        git(root, 'push', 'origin', 'HEAD:refs/heads/main', timeout=900)
        remote_commit = git(root, 'ls-remote', '--heads', 'origin', 'main').stdout.decode().split()[0]
        if commit != remote_commit:
            raise ValueError('GitHub上の保存結果が一致しません。')
        # 独立cloneへLFSを取得し直し、実体があることまで確認する。元データは消さない。
        with tempfile.TemporaryDirectory(prefix='dogido-data-verify-') as tmp:
            checked = Path(tmp) / 'repository'
            git(root, 'clone', '--quiet', '--depth', '1', '--branch', 'main', REMOTE, str(checked), env={'GIT_LFS_SKIP_SMUDGE': '1'}, timeout=300)
            git(checked, 'lfs', 'pull', timeout=900)
            if git(checked, 'rev-parse', 'HEAD').stdout.decode().strip() != commit:
                raise ValueError('再取得中にGitHubの最新版が変わりました。')
            check_files(checked, files)
        pending_path.unlink()
        working_path.unlink()
        return {'status': 'uploaded', 'url': URL, 'commit': commit, 'poems': changed_poems, 'captures': changed_captures, 'verified_files': len(files), 'verified_bytes': sum(f['bytes'] for f in files)}


def main():
    # このworkerとGit/LFSだけを同じ専用process groupにし、期限切れでまとめて回収する。
    if os.getpgrp() != os.getpid():
        os.setpgid(0, 0)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text())
        print(json.dumps(publish(plan), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'detail': str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
