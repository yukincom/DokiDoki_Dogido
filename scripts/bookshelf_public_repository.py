"""既存の本棚送信workerの公開モード。本人のfork/専用branchからPRで提供する。"""
import fcntl
import json
from pathlib import Path
import re
import tempfile
import time

from publish_bookshelf_data import REPOSITORY, URL, REMOTE, run, check_files, write_file, journal, safe_path
from bookshelf_public_data import MARKER, SCHEMA, projection, encoded


def api(path, *, method='GET', payload=None, missing_ok=False):
    args = ['gh', 'api', '--hostname', 'github.com', '--method', method, path]
    data = None
    if payload is not None:
        args += ['--input', '-']; data = encoded(payload).encode()
    response = run(args, input=data, timeout=60, check=False)
    try:
        value = json.loads(response.stdout)
    except (ValueError, UnicodeDecodeError):
        value = {}
    if response.returncode:
        if missing_ok and str(value.get('status')) == '404':
            return None
        raise RuntimeError('GitHubの処理を完了できませんでした。GitHub CLIのログイン状態と通信を確認してください。')
    return value


def git(root, *args, remote=REMOTE, user=None, **kwargs):
    options = ['credential.helper=', 'credential.helper=!gh auth git-credential',
               f'lfs.url={remote}/info/lfs', f'lfs.pushurl={remote}/info/lfs',
               f'remote.origin.lfsurl={remote}/info/lfs', f'remote.origin.lfspushurl={remote}/info/lfs',
               'core.hooksPath=/dev/null', 'commit.gpgsign=false']
    if user:
        options += [f'user.name={user["login"]}', f'user.email={user["id"]}+{user["login"]}@users.noreply.github.com']
        kwargs['env'] = {**kwargs.pop('env', {}), 'GIT_AUTHOR_NAME': user['login'], 'GIT_COMMITTER_NAME': user['login'],
                         'GIT_AUTHOR_EMAIL': f'{user["id"]}+{user["login"]}@users.noreply.github.com',
                         'GIT_COMMITTER_EMAIL': f'{user["id"]}+{user["login"]}@users.noreply.github.com'}
    command = ['git']
    for option in options:
        command += ['-c', option]
    return run(command + ['-C', str(root), *args], **kwargs)


def check_public(repository):
    if (repository.get('full_name') != REPOSITORY or repository.get('private') is not False
            or repository.get('default_branch') != 'main' or type(repository.get('id')) is not int):
        raise ValueError('公開用のデータ受付がまだ準備されていません。元の非公開保管庫へは送信しません。')


def read_index(root, ref, path):
    result = git(root, 'show', f'{ref}:{path}', check=False)
    return json.loads(result.stdout) if result.returncode == 0 else {}


def changes(current, previous):
    old = {p['id']: p for p in previous.get('poems', [])}
    changed = [p for p in current['poems'] if p != old.get(p['id'])]
    return len(changed), sum('hidden' in q for p in changed for q in p['requests'])


def own_fork(repository, user):
    if repository['owner']['id'] == user['id']:
        return repository
    name = f'{user["login"]}/{REPOSITORY.split("/")[1]}'
    fork = api(f'repos/{name}', missing_ok=True)
    if fork is None:
        fork = api(f'repos/{REPOSITORY}/forks', method='POST', payload={'name': REPOSITORY.split('/')[1], 'default_branch_only': True})
    if (fork.get('private') is not False or fork.get('owner', {}).get('id') != user['id']
            or fork.get('source', fork.get('parent', {})).get('id') != repository['id']
            or not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', fork.get('full_name', ''))):
        raise ValueError('提供先につながる本人のforkを確認できません。同名の別リポジトリは変更しません。')
    return fork


def pull_request(repository, fork, branch, user, commit, counts):
    head = f'{user["login"]}:{branch}'
    prs = api(f'repos/{REPOSITORY}/pulls?state=open&base=main&head={head}')
    if len(prs) > 1:
        raise ValueError('提供PRを一つに特定できません。')
    if prs:
        pr = prs[0]
    else:
        pr = api(f'repos/{REPOSITORY}/pulls', method='POST', payload={
            'title': '句・J-lens・内部表現の研究データを提供', 'head': head, 'base': 'main',
            'body': f'本棚から公開用の研究データを提供します。今回の変更は{counts[0]}句・採取{counts[1]}件です。\n\n'
                    '対話・メモ・材料本文・端末パス・入力token列を除外し、配列とJSONを別cloneで再取得してSHA-256を照合しました。'
                    '句・候補語・内部表現そのものの匿名化を保証するものではありません。'})
    if (pr.get('base', {}).get('repo', {}).get('id') != repository['id']
            or pr.get('head', {}).get('repo', {}).get('id') != fork['id']
            or pr.get('head', {}).get('ref') != branch or pr.get('head', {}).get('sha') != commit
            or not re.fullmatch(re.escape(URL) + r'/pull/[0-9]+', pr.get('html_url', ''))):
        raise ValueError('PRの保存先または送信commitを確認できません。')
    return pr['html_url']


def publish_public(plan):
    if plan['repository'] != REPOSITORY:
        raise ValueError('提供先が指定されたリポジトリではありません。')
    if run(['gh', 'auth', 'status', '--hostname', 'github.com'], check=False, timeout=30).returncode:
        raise RuntimeError('初回は gh auth login --hostname github.com --web --git-protocol https でログインしてください。')
    run(['git', 'lfs', 'version'], timeout=30)
    user = api('user')
    if type(user.get('id')) is not int or not re.fullmatch(r'[A-Za-z0-9-]+', user.get('login', '')):
        raise ValueError('本人のGitHubアカウントを確認できません。')
    repository = api(f'repos/{REPOSITORY}'); check_public(repository)
    public = projection(plan, user['id'])
    root = Path(plan['checkout'])
    if not root.is_absolute() or any(p.is_symlink() for p in [root, *root.parents]):
        raise ValueError('提供用の作業場所を確認できません。')
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / '.public-upload.lock'
    receipt_path = root / 'public-upload.json'
    if lock_path.is_symlink() or receipt_path.is_symlink():
        raise ValueError('提供履歴の保存先を確認できません。')
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('別のデータ提供が進行中です。')
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        scope = {'repository_id': repository['id'], 'contributor_id': user['id'], 'archive_id': public['index']['archive_id']}
        if receipt and any(receipt.get(k) != v for k, v in scope.items()):
            raise ValueError('提供履歴が別のアカウント・保管庫・リポジトリに属しています。')
        branch = f'codex/data-{user["id"]}-{public["index"]["archive_id"]}'
        with tempfile.TemporaryDirectory(prefix='dogido-public-data-') as temporary:
            checkout = Path(temporary) / 'upload'; checkout.mkdir()
            def g(*args, **kwargs):
                return git(checkout, *args, user=user, **kwargs)
            g('init', '-b', 'main')
            g('remote', 'add', 'upstream', REMOTE)
            g('fetch', '--quiet', 'upstream', 'main', env={'GIT_LFS_SKIP_SMUDGE': '1'}, timeout=300)
            g('checkout', '--quiet', '-B', 'main', 'FETCH_HEAD', env={'GIT_LFS_SKIP_SMUDGE': '1'})
            if read_index(checkout, 'HEAD', MARKER) != {'schema': SCHEMA}:
                raise ValueError('公開用の形式を確認できません。既存の非公開記録とは混ぜません。')
            if (checkout / '.lfsconfig').exists() or (checkout / '.lfsconfig').is_symlink():
                raise ValueError('LFSの送信先が変更されています。')
            if g('show', 'HEAD:.gitattributes').stdout != b'*.f32 filter=lfs diff=lfs merge=lfs -text\n':
                raise ValueError('公開データのLFS設定が想定と異なります。')
            base = g('rev-parse', 'HEAD').stdout.decode().strip()
            if read_index(checkout, 'HEAD', public['index_path']) == public['index']:
                return {'status': 'up_to_date', 'url': URL, 'commit': base, 'poems': 0, 'captures': 0}
            fork = own_fork(repository, user)
            remote = f'https://github.com/{fork["full_name"]}.git'
            g('remote', 'add', 'origin', remote)
            heads = g('ls-remote', '--heads', 'origin', branch).stdout.decode().split()
            prior = heads[0] if heads else None
            if prior:
                # tipのscope検査だけでは追加後削除された秘密を発見できない。生成済みSHAだけを取り込む。
                if prior not in receipt.get('owned_commits', []) or receipt.get('fork_id') != fork['id']:
                    raise ValueError('提供branchにこの本棚が作っていない履歴があります。自動では取り込みません。')
                g('fetch', '--quiet', 'origin', branch, env={'GIT_LFS_SKIP_SMUDGE': '1'}, timeout=300)
                if g('rev-parse', 'FETCH_HEAD').stdout.decode().strip() != prior:
                    raise ValueError('他の送信で提供branchが変わりました。もう一度操作してください。')
                g('checkout', '--quiet', '-B', branch, 'FETCH_HEAD', env={'GIT_LFS_SKIP_SMUDGE': '1'})
                g('merge', '--no-edit', base, env={'GIT_LFS_SKIP_SMUDGE': '1'})
            else:
                g('checkout', '--quiet', '-b', branch)
            previous = read_index(checkout, 'HEAD', public['index_path'])
            counts = changes(public['index'], previous)
            # filter設定だけを使い、取得したrepositoryのhookを実行しない。LFSはpush前に明示送信する。
            g('lfs', 'install', '--local', '--skip-smudge', '--skip-repo', remote=remote)
            for file in public['files']:
                # skip-smudgeで得たLFS pointerは原本ではない。現在のhashを指す場合だけ実体で置換する。
                path = safe_path(checkout, file['path'])
                if file['path'].endswith('/hidden.f32') and path.is_file() and not path.is_symlink():
                    pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{file["sha256"]}\nsize {file["bytes"]}\n'.encode()
                    if path.stat().st_size == len(pointer) and path.read_bytes() == pointer:
                        path.unlink()
                write_file(checkout, file, mutable_paths=(public['index_path'],))
            check_files(checkout, public['files'])
            g('add', '--', public['prefix'])
            changed = g('diff', '--cached', '--quiet', check=False).returncode != 0
            if changed:
                g('commit', '-m', '句とJ-lensと内部表現の公開用データを提供')
            commit = g('rev-parse', 'HEAD').stdout.decode().strip()
            paths = g('diff', '--name-only', '-z', base, 'HEAD').stdout.decode().split('\0')
            if any(p and not p.startswith(public['prefix'] + '/') for p in paths):
                raise ValueError('本人の提供領域以外の変更が含まれています。')
            verified = receipt.get('verified_commit') == commit
            # push成功直後の終了でも自身のcommitを識別できるよう先に記録する。
            receipt.update(scope, fork_id=fork['id'], commit=commit, branch=branch)
            receipt['owned_commits'] = sorted(set(receipt.get('owned_commits', [])) | {commit})
            journal(receipt_path, receipt)
            current_repository = api(f'repos/{REPOSITORY}'); check_public(current_repository)
            if current_repository['id'] != repository['id']:
                raise ValueError('データ受付リポジトリが切り替わりました。')
            if commit != prior:
                # forkの作成は非同期。空ならGitHub側の準備だけを短く待つ。
                for attempt in range(6):
                    if g('ls-remote', '--heads', 'origin', 'main').stdout.strip():
                        break
                    if attempt == 5:
                        raise RuntimeError('GitHubでforkを準備中です。少し待ってから再操作してください。')
                    time.sleep(2)
                g('lfs', 'push', 'origin', 'HEAD', remote=remote, timeout=900)
                # --forceは付けない。fetch時点から別の更新があれば通常pushが拒否する。
                g('push', 'origin', f'HEAD:refs/heads/{branch}', remote=remote, timeout=900)
                if g('ls-remote', '--heads', 'origin', branch).stdout.decode().split()[0] != commit:
                    raise ValueError('GitHub上の送信commitを確認できません。')
            if not verified:
                checked = Path(temporary) / 'verify'
                g('clone', '--quiet', '--depth', '1', '--branch', branch, remote, str(checked), remote=remote,
                  env={'GIT_LFS_SKIP_SMUDGE': '1'}, timeout=300)
                git(checked, 'lfs', 'pull', '--include', public['prefix'] + '/**', '--exclude', '', remote=remote, timeout=900)
                if git(checked, 'rev-parse', 'HEAD').stdout.decode().strip() != commit:
                    raise ValueError('再取得中に提供branchが変わりました。')
                check_files(checked, public['files'])
                receipt['verified_commit'] = commit; journal(receipt_path, receipt)
            pr_url = pull_request(repository, fork, branch, user, commit, counts)
            return {'status': 'submitted' if commit != prior else 'up_to_date', 'url': pr_url, 'commit': commit,
                    'poems': counts[0], 'captures': counts[1], 'verified_files': len(public['files'])}
