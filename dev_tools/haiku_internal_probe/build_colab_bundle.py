"""Colabへ渡す検証用ファイルを固定し、比較元を含むZIPとnotebookを作る。

prepareは既存実測のtoken列・重み識別を再利用する。packageはMac比較元の
完了記録を要求し、秘密情報・常駐設定・モデル本体を含めず配布物を構成する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import textwrap
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OLD = ROOT / '.dogido_tools/experiments/2026-10-16-haiku-jspace'
EXPERIMENT = ROOT / '.dogido_tools/experiments/2026-10-17-qat-jspace'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def prepare(destination, model, site_packages):
    root = destination / 'qat-colab'
    root.mkdir(parents=True, exist_ok=True)
    binding = json.loads((OLD / 'reference/checkpoint-binding.json').read_text())
    files = binding['model_files']
    for path in model.glob('*.safetensors.index.json'):
        files[path.name] = {'sha256': sha(path), 'size': path.stat().st_size}
    native = site_packages / 'mlx_lm'
    implementation = ['models/gemma4.py', 'models/gemma4_text.py', 'models/switch_layers.py',
                      'models/base.py', 'models/cache.py', 'models/rope_utils.py', 'utils.py']
    spec = {
        'purpose': 'compatibility_and_speed_only; no model weight updates; no full lens fit',
        'model_repo': 'lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit',
        'model_revision': model.name,
        'model_files': files,
        'versions': {'mlx': '0.32.0', 'mlx-lm': '0.31.3', 'transformers': '5.14.1',
                     'numpy': '2.4.4', 'huggingface-hub': '1.12.0',
                     'safetensors': '0.8.0', 'tokenizers': '0.22.2'},
        'implementation_sha256': {name: sha(native / name) for name in implementation},
        'forward_token_ids': json.loads((OLD / 'validation/validation.json').read_text())['input']['token_ids'],
        'jacobian_token_ids': json.loads((EXPERIMENT / 'benchmark-compiled/benchmark.json').read_text())['prompt_token_ids'],
        'position_scope': 'Existing unmodified prompt prefixes: 32 and 128 tokens. Not full Dogido scene inputs.',
        'jacobian_scope': 'All 29 source layers, first 32 of 2816 target coordinates, batch=1, no compile wrapper.',
        'comparison_boundary': 'decoder output after layer_scalar, before final RMSNorm',
        'moe_derivative': 'discrete expert indices stopped; selected expert weights remain differentiable',
    }
    if model.name != 'aa5e2e50376f54254d503276ca2222ea8569b95e':
        raise ValueError('unexpected model revision')
    (root / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    for name in ['colab_probe.py', 'qat_jacobian.py']:
        shutil.copyfile(HERE / name, root / name)
    return root


def package(destination):
    root = destination / 'qat-colab'
    report = json.loads((root / 'baseline/report.json').read_text())
    if report['status'] != 'measurement_complete_review_required' or report['backend'] != 'metal':
        raise ValueError('a completed native Mac baseline is required')
    if report['spec_sha256'] != sha(root / 'spec.json'):
        raise ValueError('baseline spec changed')
    for name in ['colab_probe.py', 'qat_jacobian.py']:
        if sha(HERE / name) != sha(root / name):
            raise ValueError('rebuild baseline after changing probe code')
    included = ['spec.json', 'colab_probe.py', 'qat_jacobian.py', 'baseline/report.json',
                'baseline/forward.npz', 'baseline/jacobian_rows.npy']
    hashes = {name: sha(root / name) for name in included}
    (root / 'files.sha256.json').write_text(json.dumps(hashes, indent=2))
    zip_path = destination / 'qat_colab_support.zip'
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in included + ['files.sha256.json']:
            archive.write(root / name, 'qat-colab/' + name)
    zip_hash = sha(zip_path)
    cells = []

    def add(kind, content):
        cell = {'cell_type': kind, 'metadata': {}, 'source': textwrap.dedent(content).strip() + '\n'}
        if kind == 'code':
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)

    add('markdown', '''
    # 今のGemma 4 QAT重みをColabで検証する

    **まず互換性と速度だけを測定します。本学習は開始しません。**

    1. Colabの「ランタイム」→「ランタイムのタイプを変更」でGPUを選択（A100を推奨）。
    2. 上からセルを実行し、アップロード画面で `qat_colab_support.zip` を選びます。
    3. 最後にダウンロードされる `qat_colab_result.zip` をこのチャットへ添付してください。

    同じrevisionのQAT重み（約15.6 GB）はHugging Faceから取得します。手動アップロードは不要です。
    現在のMacの常駐サービスへは接続しません。モデルの重みは更新しません。
    比較対象は短い既存入力の数値計算で、感情の判定やレンズの精度評価ではありません。
    CUDA上の実行は未検証です。途中で失敗した場合も最後のセルで結果を保存できます。
    ''')
    add('code', f'''
    from pathlib import Path
    import hashlib, json, os, platform, shutil, subprocess, sys, time, zipfile
    from google.colab import files
    WORK = Path('/content/qat-colab-check')
    WORK.mkdir(exist_ok=True)
    uploaded = files.upload()
    if len(uploaded) != 1:
        raise ValueError('qat_colab_support.zip だけを選んでください')
    payload = next(iter(uploaded.values()))
    expected = '{zip_hash}'
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError('ZIPがこのノートブックの版と一致しません')
    package_path = WORK / 'support.zip'
    package_path.write_bytes(payload)
    with zipfile.ZipFile(package_path) as archive:
        for item in archive.infolist():
            target = (WORK / item.filename).resolve()
            if not target.is_relative_to(WORK.resolve()) or not item.filename.startswith('qat-colab/'):
                raise ValueError('不正なZIP内パス')
        archive.extractall(WORK)
    BUNDLE = WORK / 'qat-colab'
    for name, expected in json.loads((BUNDLE / 'files.sha256.json').read_text()).items():
        if hashlib.sha256((BUNDLE / name).read_bytes()).hexdigest() != expected:
            raise ValueError('ファイル不一致: ' + name)
    SPEC = json.loads((BUNDLE / 'spec.json').read_text())
    RESULT = WORK / ('result-' + time.strftime('%Y%m%d-%H%M%S', time.gmtime()))
    RESULT.mkdir(exist_ok=False)
    DEPS = WORK / 'python-packages'
    ENV = {{**os.environ, 'PYTHONPATH': str(DEPS) + os.pathsep + str(BUNDLE),
           'PYTHONNOUSERSITE': '1', 'MLX_ENABLE_TF32': '0', 'USE_TORCH': '0', 'USE_TF': '0'}}
    def run_logged(name, command):
        with (RESULT / (name + '.log')).open('w') as log:
            process = subprocess.Popen(command, env=ENV, cwd=WORK, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                print(line, end=''); log.write(line); log.flush()
            code = process.wait()
        if code:
            raise RuntimeError(f'{{name}} が失敗しました。最後のセルで結果を保存してください。exit={{code}}')
    run_logged('gpu', ['nvidia-smi'])
    print('準備完了:', SPEC['model_repo'], SPEC['model_revision'])
    ''')
    add('markdown', '''
    ## 固定バージョンのMLX CUDAを準備

    専用フォルダへ依存を入れ、検査は別プロセスで実行します。Notebookの既存Python環境は置き換えません。
    CUDA 12版を使います。高速化用TF32は比較のため無効にします。
    ''')
    add('code', '''
    requirements = ['mlx[cuda12]==0.32.0'] + [f'{name}=={version}' for name, version in SPEC['versions'].items() if name != 'mlx']
    run_logged('install', [sys.executable, '-m', 'pip', 'install', '--target', str(DEPS), '--upgrade', *requirements])
    run_logged('small_cuda_operator', [sys.executable, '-c', """
    import mlx.core as mx
    if not mx.cuda.is_available():
        raise RuntimeError('CUDA版MLXを利用できません')
    mx.set_default_device(mx.gpu)
    x = mx.ones((2, 1, 64), dtype=mx.bfloat16)
    w = mx.ones((4, 64, 64), dtype=mx.bfloat16)
    q, s, b = mx.quantize(w, group_size=64, bits=4)
    ids = mx.array([0, 1], dtype=mx.uint32)
    def f(value):
        return mx.gather_qmm(value, q, s, b, rhs_indices=ids, transpose=True, group_size=64, bits=4)
    y, gradients = mx.vjp(f, [x], [mx.ones_like(x)])
    mx.eval(y, gradients)
    assert bool(mx.all(mx.isfinite(y)).item()) and bool(mx.all(mx.isfinite(gradients[0])).item())
    print('CUDA量子化MoE演算と入力VJP: 計算完了（モデル全体は次のセルで確認）')
    """])
    ''')
    add('markdown', '''
    ## 同じQAT重みを取得

    約15.6 GBをColabへダウンロードします。取得後、Macで使用したファイルのSHA-256と全て照合します。
    ダウンロードが成功しても、CUDAでの数値互換性が確認できたことにはなりません。
    ''')
    add('code', '''
    run_logged('download', [sys.executable, '-c', """
    import json
    from pathlib import Path
    from huggingface_hub import snapshot_download
    spec = json.loads(Path('qat-colab/spec.json').read_text())
    path = snapshot_download(repo_id=spec['model_repo'], revision=spec['model_revision'],
                             allow_patterns=list(spec['model_files']), local_dir='model')
    print('取得完了:', path)
    """])
    ''')
    add('markdown', '''
    ## 短い互換性・速度検査

    32tokenで全30層の内部状態・末尾の語彙スコア・expert選択を比較します。
    続いて128tokenでJacobianの32座標だけを計算します。全2816座標の学習は実行しません。
    速度から表示する100文章の時間は単一入力からの外挿で、所要時間の保証ではありません。
    数値差を記録し、人が確認するところで止まります。
    ''')
    add('code', '''
    run_logged('probe', [sys.executable, str(BUNDLE / 'colab_probe.py'),
                        '--bundle', str(BUNDLE), '--model', str(WORK / 'model'),
                        '--output', str(RESULT / 'measurement'),
                        '--reference', str(BUNDLE / 'baseline'), '--require-cuda'])
    print('検査終了。本学習は開始していません。次のセルで結果を保存してください。')
    ''')
    add('markdown', '''
    ## 結果をダウンロード（途中で失敗した場合も実行）

    `qat_colab_result.zip` をダウンロードし、このチャットへ添付してください。
    成否のログ、Macとの比較値、再比較用の数値を含みます。モデル重みは含めません。
    ''')
    add('code', '''
    shutil.copyfile(BUNDLE / 'spec.json', RESULT / 'spec.json')
    archive = shutil.make_archive(str(WORK / 'qat_colab_result'), 'zip', RESULT)
    files.download(archive)
    ''')
    notebook = {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {
        'kernelspec': {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'},
        'language_info': {'name': 'python'},
        'colab': {'name': 'qat_colab_check.ipynb', 'provenance': []},
        'accelerator': 'GPU'}, 'cells': cells}
    for i, cell in enumerate(cells):
        cell['id'] = f'qat-check-{i:02d}'
    path = destination / 'qat_colab_check.ipynb'
    path.write_text(json.dumps(notebook, ensure_ascii=False, indent=2))
    print(json.dumps({'notebook': str(path), 'support_zip': str(zip_path),
                      'zip_sha256': zip_hash, 'zip_bytes': zip_path.stat().st_size}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['prepare', 'package'])
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--model', type=Path)
    parser.add_argument('--site-packages', type=Path)
    args = parser.parse_args()
    if args.stage == 'prepare':
        if not args.model or not args.site_packages:
            parser.error('prepare requires --model and --site-packages')
        print(prepare(args.destination, args.model, args.site_packages))
    else:
        package(args.destination)
