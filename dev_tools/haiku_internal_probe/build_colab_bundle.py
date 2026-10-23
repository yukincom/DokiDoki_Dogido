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
import sys
import textwrap
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from dogido_server.tls_security import bootstrap_pip_command, MIN_CERTIFI
OLD = ROOT / '.dogido_tools/experiments/2026-10-16-haiku-jspace'
EXPERIMENT = ROOT / '.dogido_tools/experiments/2026-10-17-qat-jspace'

# Macの既存補正をColabの専用依存へ再現する。修正前後の両hashで対象を閉じる。
SOURCE_ALIGNMENT = r'''
module_path = DEPS / 'mlx_lm/models/gemma4_text.py'
source = module_path.read_text()
expected = SPEC['implementation_sha256']['models/gemma4_text.py']
actual = hashlib.sha256(source.encode()).hexdigest()
if actual != expected:
    if actual != '77f46bc3f162a0b9513157dade4be2c381d4df3295262c69034a53d46111370f':
        raise RuntimeError('Unexpected Gemma4 source; refusing automatic patch')
    source = source.replace('        sanitized = {}\n', '        sanitized = {}\n        first_kv_shared = self.args.num_hidden_layers - self.args.num_kv_shared_layers\n', 1)
    anchor = '            if k.endswith(".experts.gate_up_proj"):\n'
    extra = [
        '            # KV-shared layers reuse K/V from earlier layers — drop their projections',
        '            if any(',
        '                s in k',
        '                for s in (".self_attn.k_proj", ".self_attn.v_proj", ".self_attn.k_norm")',
        '            ):',
        '                try:',
        '                    layer_idx = int(k.split("layers.")[1].split(".")[0])',
        '                    if layer_idx >= first_kv_shared:',
        '                        continue',
        '                except (IndexError, ValueError):',
        '                    pass',
        '',
    ]
    source = source.replace(anchor, '\n'.join(extra) + '\n' + anchor, 1)
    if hashlib.sha256(source.encode()).hexdigest() != expected:
        raise RuntimeError('Patch result does not match the Mac source')
    module_path.write_text(source)
print('Gemma4 implementation SHA-256 matches the Mac baseline.')
'''


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


def prepare_optimization(destination, baseline_bundle):
    """既存比較元と保存済みtest文章から、同じ入力を再測定する束を作る。"""
    root = destination / 'qat-colab'
    root.mkdir(parents=True, exist_ok=False)
    for name in ['colab_probe.py', 'qat_jacobian.py']:
        if sha(HERE / name) != sha(baseline_bundle / name):
            raise ValueError('original baseline probe changed')
    shutil.copyfile(baseline_bundle / 'spec.json', root / 'spec.json')
    spec = json.loads((root / 'spec.json').read_text())
    test = json.loads((EXPERIMENT / 'data/test.json').read_text())[0]
    expected_text = '2c27eca43dcb8c265876f8aa8816bb3c322da775f65a2c0939283383c18ec8ef'
    if hashlib.sha256(test['text'].encode()).hexdigest() != expected_text:
        raise ValueError('held-out WikiText case changed')
    cases = {
        'scope': 'two fixed 128-token prefixes; dispersed coordinates; no generation or fitting',
        'coordinates': [round(i * 2815 / 31) for i in range(32)],
        'cases': [
            {'id': 'existing_production_prefix', 'token_ids': spec['jacobian_token_ids']},
            {'id': 'held_out_wikitext_prefix', 'source_row': test['source_row'],
             'text': test['text'], 'text_sha256': expected_text},
        ],
    }
    (root / 'optimization-cases.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2))
    for name in ['colab_probe.py', 'qat_jacobian.py', 'qat_vectorized.py',
                 'optimization_probe.py', 'test_qat_vectorized.py']:
        shutil.copyfile(HERE / name, root / name)
    return root


def package(destination, optimization=False):
    root = destination / 'qat-colab'
    report = json.loads((root / 'baseline/report.json').read_text())
    expected_status = ('optimization_measurement_complete_review_required' if optimization
                       else 'measurement_complete_review_required')
    if report['status'] != expected_status or report['backend'] != 'metal':
        raise ValueError('a completed native Mac baseline is required')
    if report['spec_sha256'] != sha(root / 'spec.json'):
        raise ValueError('baseline spec changed')
    for name in ['colab_probe.py', 'qat_jacobian.py']:
        if sha(HERE / name) != sha(root / name):
            raise ValueError('rebuild baseline after changing probe code')
    included = ['spec.json', 'colab_probe.py', 'qat_jacobian.py', 'baseline/report.json',
                'baseline/forward.npz', 'baseline/jacobian_rows.npy']
    if optimization:
        if report['cases_sha256'] != sha(root / 'optimization-cases.json'):
            raise ValueError('optimization cases changed')
        for name, expected in report['source_sha256'].items():
            if sha(HERE / name) != expected or sha(root / name) != expected:
                raise ValueError('optimization source changed after measurement: ' + name)
        included = ['spec.json', 'colab_probe.py', 'qat_jacobian.py', 'qat_vectorized.py',
                    'optimization_probe.py', 'test_qat_vectorized.py',
                    'optimization-cases.json', 'baseline/report.json']
        for i in range(2):
            included.append(f'baseline/case-{i}-forward.npz')
            included.extend(f'baseline/case-{i}-{label}.npy'
                            for label in ['native_1', 'vector_1', 'vector_2', 'native_2'])
    hashes = {name: sha(root / name) for name in included}
    (root / 'files.sha256.json').write_text(json.dumps(hashes, indent=2))
    support_name = 'qat_colab_optimization_support.zip' if optimization else 'qat_colab_support.zip'
    result_name = 'qat_colab_optimization_result' if optimization else 'qat_colab_result'
    notebook_name = 'qat_colab_optimization.ipynb' if optimization else 'qat_colab_check.ipynb'
    zip_path = destination / support_name
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
    CUDAで計算できてもMacとの数値一致は別途確認が必要です。途中で失敗した場合も最後のセルで結果を保存できます。
    ''')
    if optimization:
        cells[-1]['source'] = textwrap.dedent('''
        # 同じQAT重みでJacobianの方向をまとめる改善を測定する

        **速度と数値差だけを測ります。本学習は開始しません。**

        GPUランタイムで上から実行し、最初のセルでは `qat_colab_optimization_support.zip` を選びます。
        固定revisionの重み約15.6 GBを取得します。重み更新・Macの常駐サービスへの接続はありません。
        既存の文章と独立したWikiText文章で、従来方式と改善版をそれぞれ二回測ります。
        Macでの改善がCUDAでも成立するかは未確認です。結果を取得して比較するところで止まります。
        ''').strip() + '\n'
    add('code', f'''
    from pathlib import Path
    import hashlib, json, os, platform, shutil, subprocess, sys, time, zipfile
    from google.colab import files
    WORK = Path('/content/qat-colab-{'optimization' if optimization else 'check'}')
    WORK.mkdir(exist_ok=True)
    uploaded = files.upload()
    if len(uploaded) != 1:
        raise ValueError('{support_name} だけを選んでください')
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
    CUDA 12版と対応する12.9のruntimeヘッダーを同じ場所へ入れます。
    Colab標準の別バージョンのヘッダーを拾わないよう参照先を固定し、高速化用TF32は比較のため無効にします。
    ''')
    add('code', '''
    requirements = ['mlx[cuda12]==0.32.0', 'nvidia-cuda-runtime-cu12==12.9.79', 'nvidia-cuda-nvrtc-cu12==12.9.86'] + [f'{name}=={version}' for name, version in SPEC['versions'].items() if name != 'mlx']
    INSTALLER = WORK / 'verified-pip'
    run_logged('bootstrap_pip', __BOOTSTRAP_PIP_COMMAND__)
    run_logged('install', [sys.executable, '-S', str(INSTALLER / 'pip'), '--isolated', '--disable-pip-version-check', 'install', '--target', str(DEPS), '--upgrade', 'certifi>=__MIN_CERTIFI__', *requirements])
    CUDA_ROOT = DEPS / 'nvidia' / 'cuda_runtime'
    if not (CUDA_ROOT / 'include' / 'cuda_fp8.hpp').is_file():
        raise RuntimeError('CUDA 12.9 runtime headers are missing')
    ENV['CUDA_HOME'] = str(CUDA_ROOT)
    ENV['CUDA_PATH'] = str(CUDA_ROOT)
    ENV['MLX_PTX_CACHE_DIR'] = str(WORK / 'mlx-ptx-cuda12-9')
    run_logged('small_cuda_operator', [sys.executable, '-c', """
    import os
    import mlx.core as mx
    print('CUDA_HOME:', os.environ['CUDA_HOME'])
    if not mx.cuda.is_available():
        raise RuntimeError('CUDA版MLXを利用できません')
    mx.set_default_device(mx.gpu)
    x = mx.ones((2, 1, 64), dtype=mx.bfloat16)
    w = mx.ones((4, 64, 64), dtype=mx.bfloat16)
    q, s, b = mx.quantize(w, group_size=64, bits=4)
    ids = mx.array([0, 1], dtype=mx.uint32)
    def f(value):
        return mx.gather_qmm(value, q, s, b, rhs_indices=ids, transpose=True, group_size=64, bits=4)
    outputs, gradients = mx.vjp(f, [x], [mx.ones_like(x)])
    mx.eval(outputs, gradients)
    assert all(bool(mx.all(mx.isfinite(value)).item()) for value in outputs + gradients)
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
    Macにある共有KV層の読み込み補正も再現し、実装ファイルを完全一致させます。今回の共有KV層0のモデルでは作用しない補正です。
    速度から表示する100文章の時間は単一入力からの外挿で、所要時間の保証ではありません。
    数値差を記録し、人が確認するところで止まります。
    ''')
    if optimization:
        cells[-1]['source'] = textwrap.dedent('''
        ## 二入力・32座標の比較

        それぞれ128tokenで全30層の残差とexpert選択・重みが変わらないことを確認します。
        続いて全29source層・分散した32座標を、従来方式と16方向をまとめる方式で二回ずつ測定します。
        warmupと定常時間、同じ方式の繰返し差、方式間の数値差を保存します。
        小さな演算の検査が失敗した場合はモデル全体の測定へ進みません。本学習は開始しません。
        ''').strip() + '\n'
    add('code', SOURCE_ALIGNMENT + textwrap.dedent('''
    run_logged('probe', [sys.executable, str(BUNDLE / 'colab_probe.py'),
                        '--bundle', str(BUNDLE), '--model', str(WORK / 'model'),
                        '--output', str(RESULT / 'measurement'),
                        '--reference', str(BUNDLE / 'baseline'), '--require-cuda'])
    print('検査終了。本学習は開始していません。次のセルで結果を保存してください。')
    '''))
    if optimization:
        cells[-1]['source'] = SOURCE_ALIGNMENT + textwrap.dedent('''
        run_logged('unit_checks', [sys.executable, '-m', 'unittest', 'discover',
                                   '-s', str(BUNDLE), '-p', 'test_qat_vectorized.py', '-v'])
        run_logged('optimization_probe', [sys.executable, str(BUNDLE / 'optimization_probe.py'),
                                          '--bundle', str(BUNDLE), '--model', str(WORK / 'model'),
                                          '--output', str(RESULT / 'measurement'), '--require-cuda'])
        print('測定終了。本学習は開始していません。次のセルで結果を保存してください。')
        ''')
    add('markdown', f'''
    ## 結果をダウンロード（途中で失敗した場合も実行）

    `{result_name}.zip` をダウンロードし、このチャットへ添付してください。
    成否のログ、Macとの比較値、再比較用の数値を含みます。モデル重みは含めません。
    ''')
    if optimization:
        cells[-1]['source'] = cells[-1]['source'].replace('Macとの比較値', '同じGPU内の方式間比較値')
    add('code', f'''
    shutil.copyfile(BUNDLE / 'spec.json', RESULT / 'spec.json')
    archive = shutil.make_archive(str(WORK / '{result_name}'), 'zip', RESULT)
    files.download(archive)
    ''')
    if optimization:
        cells[-1]['source'] = "shutil.copyfile(BUNDLE / 'optimization-cases.json', RESULT / 'optimization-cases.json')\n" + cells[-1]['source']
    notebook = {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {
        'kernelspec': {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'},
        'language_info': {'name': 'python'},
        'colab': {'name': notebook_name, 'provenance': []},
        'accelerator': 'GPU'}, 'cells': cells}
    bootstrap_command = repr(bootstrap_pip_command("__PYTHON__", target="__INSTALLER__"))
    bootstrap_command = bootstrap_command.replace("'__PYTHON__'", "sys.executable").replace("'__INSTALLER__'", "str(INSTALLER)")
    for cell in cells:
        if cell['cell_type'] == 'code':
            cell['source'] = cell['source'].replace('__BOOTSTRAP_PIP_COMMAND__', bootstrap_command).replace('__MIN_CERTIFI__', MIN_CERTIFI)
    for i, cell in enumerate(cells):
        cell['id'] = f'qat-check-{i:02d}'
    path = destination / notebook_name
    path.write_text(json.dumps(notebook, ensure_ascii=False, indent=2))
    print(json.dumps({'notebook': str(path), 'support_zip': str(zip_path),
                      'zip_sha256': zip_hash, 'zip_bytes': zip_path.stat().st_size}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['prepare', 'package', 'prepare-optimization', 'package-optimization'])
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--model', type=Path)
    parser.add_argument('--site-packages', type=Path)
    parser.add_argument('--baseline-bundle', type=Path)
    args = parser.parse_args()
    if args.stage == 'prepare':
        if not args.model or not args.site_packages:
            parser.error('prepare requires --model and --site-packages')
        print(prepare(args.destination, args.model, args.site_packages))
    elif args.stage == 'prepare-optimization':
        if not args.baseline_bundle:
            parser.error('prepare-optimization requires --baseline-bundle')
        print(prepare_optimization(args.destination, args.baseline_bundle))
    else:
        package(args.destination, optimization=args.stage == 'package-optimization')
