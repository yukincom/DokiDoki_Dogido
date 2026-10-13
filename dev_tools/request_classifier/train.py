"""ModernBERT-Jaを既存5分類データで学習・評価する独立実験。

入力はdataset.pyの学習本文と固定4分割。trainで更新し、devだけで設定を
確定してからtest/regressionを予測する。モデルは指定実験領域へ保存し、
結果JSON・全予測・誤分類Markdownを出す。本体サービスへの接続は行わない。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import time

import numpy as np
import torch
from huggingface_hub import HfApi, snapshot_download
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          DataCollatorWithPadding, Trainer, TrainerCallback,
                          TrainingArguments, set_seed)

from dataset import LABEL_TO_ID, ROOT, model_example, read_splits, report, validate
from evaluation import LABELS, classification_metrics, selection_key, summarize


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EncodedRows(torch.utils.data.Dataset):
    """唯一の入力整形関数で本文を作る。現在発話が欠けないよう切り詰めを拒否する。"""
    def __init__(self, rows: list[dict], tokenizer, max_length: int):
        self.values = []
        self.lengths = []
        for row in rows:
            item = model_example(row)
            tokens = tokenizer(item['text'], truncation=False)
            length = len(tokens['input_ids'])
            if length > max_length:
                raise ValueError(f"{row['id']}: {length} tokens exceed {max_length}; current text must be preserved")
            tokens['labels'] = item['labels']
            self.values.append(tokens)
            self.lengths.append(length)

    def __len__(self):
        return len(self.values)

    def __getitem__(self, index):
        return self.values[index]


def compute_metrics(prediction) -> dict:
    """Trainerから渡るdevの予測だけを学習中の選択指標へ変換する。"""
    estimated = np.argmax(prediction.predictions, axis=-1)
    result = classification_metrics([LABELS[i] for i in prediction.label_ids],
                                    [LABELS[i] for i in estimated], LABELS)
    return {'accuracy': result['accuracy'], 'macro_f1': result['macro_f1']}


class SaveBestDevelopment(TrainerCallback):
    """各epochのdev比較で最良の重みだけを保存し、選択根拠を全て記録する。"""
    def __init__(self, trial_dir: Path, trial_index: int, learning_rate: float, tokenizer):
        self.directory = trial_dir
        self.index = trial_index
        self.learning_rate = learning_rate
        self.tokenizer = tokenizer
        self.best = None

    def on_evaluate(self, args, state, control, metrics=None, model=None, **kwargs):
        candidate = {'selection_split': 'dev', 'trial_index': self.index,
                     'learning_rate': self.learning_rate, 'epoch': float(state.epoch),
                     'step': int(state.global_step), 'macro_f1': float(metrics['eval_macro_f1']),
                     'accuracy': float(metrics['eval_accuracy']), 'loss': float(metrics['eval_loss'])}
        # test/regressionはこのcallbackへ渡さず、最良モデル確定後の予測だけに使う。
        with (self.directory / 'dev_history.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(candidate, ensure_ascii=False, allow_nan=False) + '\n')
        if self.best is None or selection_key(candidate) > selection_key(self.best):
            self.best = candidate
            model.save_pretrained(self.directory / 'best')
            self.tokenizer.save_pretrained(self.directory / 'best')
            write_json(self.directory / 'best_development.json', candidate)
        print(json.dumps({'event': 'dev_epoch', **candidate}, ensure_ascii=False), flush=True)
        return control


def prediction_rows(rows: list[dict], output) -> list[dict]:
    """評価後にだけ注釈を予測へ結び付ける。全確率は未校正のsoftmax値。"""
    scores = torch.softmax(torch.from_numpy(output.predictions), dim=-1).numpy()
    result = []
    for row, values in zip(rows, scores, strict=True):
        predicted = LABELS[int(values.argmax())]
        result.append({'id': row['id'], 'text': row['text'], 'context': row['context'],
                       'gold': row['label'], 'predicted': predicted,
                       'correct': row['label'] == predicted, 'confidence': float(values.max()),
                       'probabilities': {label: float(values[i]) for i, label in enumerate(LABELS)},
                       'tags': row['tags'], 'needs_clarification': row['needs_clarification'],
                       'source': row['source'], 'group_id': row['group_id']})
    return result


def write_misclassifications(path: Path, predictions: dict[str, list[dict]]) -> None:
    """test・回帰・dev・trainを区別し、誤分類を履歴付きで全件残す。"""
    lines = ['# ModernBERT-Ja-30M：誤分類一覧', '',
             'devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。', '']
    for split in ['test', 'regression', 'dev', 'train']:
        mistakes = [r for r in predictions[split] if not r['correct']]
        lines += [f'## {split}：{len(mistakes)} / {len(predictions[split])}件', '']
        for row in mistakes:
            lines += [f"### {row['id']}", '']
            for turn in row['context']:
                lines += [f"- 履歴（{turn['role']}）: {turn['text']}"]
            lines += [f"- 発話: {row['text']}", f"- 正解: `{row['gold']}`",
                      f"- 予測: `{row['predicted']}`（{row['confidence']:.1%}）", '']
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--report-dir', type=Path, required=True)
    parser.add_argument('--model', default='sbintuitions/modernbert-ja-30m')
    parser.add_argument('--revision', help='省略時は開始時のHub commitを取得して全試行で固定')
    parser.add_argument('--learning-rates', type=float, nargs='+', default=[2e-5, 5e-5])
    parser.add_argument('--epochs', type=int, default=10)
    parser.add_argument('--batch-size', type=int, default=16)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--max-length', type=int, default=128)
    parser.add_argument('--device', choices=['cpu', 'mps'], default='mps')
    args = parser.parse_args()
    if args.output_dir.exists() or args.report_dir.exists():
        raise ValueError('output and report directories must be new; previous results are never overwritten')
    args.output_dir.mkdir(parents=True)
    args.report_dir.mkdir(parents=True)
    started = time.perf_counter()
    torch.set_num_threads(4)
    if args.device == 'mps' and not torch.backends.mps.is_available():
        raise RuntimeError('requested MPS is unavailable; do not silently change the experiment device')
    splits = read_splits()
    validate(splits)
    saved_report = json.loads((ROOT / 'data_report.json').read_text(encoding='utf-8'))
    if report(splits) != saved_report:
        raise ValueError('source dataset differs from reviewed report')
    revision = args.revision or HfApi(token=False).model_info(args.model).sha
    base = snapshot_download(args.model, revision=revision, token=False,
        cache_dir=str(args.output_dir.parent / 'hf-cache'),
        allow_patterns=['config.json', 'model.safetensors', 'tokenizer.json', 'tokenizer_config.json',
                        'special_tokens_map.json', 'tokenizer.model', 'spiece.model', 'README.md', 'LICENSE'])
    tokenizer = AutoTokenizer.from_pretrained(base, local_files_only=True)
    encoded = {split: EncodedRows(rows, tokenizer, args.max_length) for split, rows in splits.items()}
    manifest = {'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'model_id': args.model, 'model_revision': revision,
        'dataset_git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
        'data_sha256': {split: saved_report[split]['sha256'] for split in splits},
        'source_sha256': {name: digest(ROOT / name) for name in ['train.py', 'evaluation.py', 'dataset.py', 'labels.json']},
        'packages': {package: importlib.metadata.version(package) for package in
                     ['torch', 'transformers', 'accelerate', 'numpy', 'scikit-learn', 'huggingface-hub', 'tokenizers']},
        'python': platform.python_version(), 'platform': platform.platform(),
        'device_requested': args.device, 'torch_threads': torch.get_num_threads(),
        'seed': args.seed, 'learning_rates': args.learning_rates, 'max_epochs': args.epochs,
        'batch_size': args.batch_size, 'max_length': args.max_length,
        'token_lengths': {split: {'min': min(data.lengths), 'max': max(data.lengths),
                                 'mean': float(np.mean(data.lengths)), 'truncated': 0} for split, data in encoded.items()},
        'selection': 'dev macro_f1 descending; dev loss ascending; epoch ascending; trial index ascending',
        'optimizer': 'adamw_torch', 'scheduler': 'linear', 'warmup_steps': 0, 'weight_decay': .01,
        'dtype': 'float32', 'attention': 'sdpa', 'classification': 'single_label_argmax_no_threshold',
        'full_finetuning': True, 'test_regression_used_for_selection': False}
    write_json(args.output_dir / 'manifest.json', manifest)
    write_json(args.report_dir / 'manifest.json', manifest)
    collator = DataCollatorWithPadding(tokenizer=tokenizer)
    trials = []
    for index, learning_rate in enumerate(args.learning_rates):
        set_seed(args.seed)
        trial_dir = args.output_dir / f'trial-{index + 1}'
        trial_dir.mkdir()
        model = AutoModelForSequenceClassification.from_pretrained(base, local_files_only=True,
            num_labels=len(LABELS), id2label=dict(enumerate(LABELS)), label2id=LABEL_TO_ID,
            dtype=torch.float32, attn_implementation='sdpa')
        manifest['parameters'] = sum(parameter.numel() for parameter in model.parameters())
        callback = SaveBestDevelopment(trial_dir, index, learning_rate, tokenizer)
        training_args = TrainingArguments(output_dir=str(trial_dir), learning_rate=learning_rate,
            per_device_train_batch_size=args.batch_size, per_device_eval_batch_size=args.batch_size,
            num_train_epochs=args.epochs, weight_decay=.01, warmup_steps=0, optim='adamw_torch',
            lr_scheduler_type='linear', eval_strategy='epoch', save_strategy='no',
            logging_strategy='epoch', report_to='none', push_to_hub=False, seed=args.seed,
            data_seed=args.seed, use_cpu=args.device == 'cpu', dataloader_num_workers=0,
            dataloader_pin_memory=False, disable_tqdm=True, torch_compile=False)
        write_json(trial_dir / 'training_args.json', training_args.to_dict())
        trainer = Trainer(model=model, args=training_args, train_dataset=encoded['train'],
            eval_dataset=encoded['dev'], processing_class=tokenizer,
            data_collator=collator, compute_metrics=compute_metrics, callbacks=[callback])
        actual_device = str(next(trainer.model.parameters()).device)
        if not actual_device.startswith(args.device):
            raise RuntimeError(f'requested {args.device}, actual {actual_device}')
        manifest['device_actual'] = actual_device
        print(json.dumps({'event': 'training_started', 'trial': index + 1,
                          'learning_rate': learning_rate, 'device': actual_device}, ensure_ascii=False), flush=True)
        trial_start = time.perf_counter()
        outcome = trainer.train()
        if callback.best is None:
            raise RuntimeError('no dev evaluation was recorded')
        trial = dict(callback.best, trial=f'trial-{index + 1}',
                     train_seconds=time.perf_counter() - trial_start)
        trials.append(trial)
        write_json(trial_dir / 'train_metrics.json', outcome.metrics)
        write_json(trial_dir / 'trainer_history.json', trainer.state.log_history)
        shutil.copyfile(trial_dir / 'dev_history.jsonl', args.report_dir / f'trial-{index + 1}-dev-history.jsonl')
        del trainer, model, callback
        gc.collect()
        if args.device == 'mps':
            torch.mps.empty_cache()
    selected = max(trials, key=selection_key)
    # このファイルを確定した後に初めてtest/regressionのモデル予測へ進む。
    write_json(args.output_dir / 'selection.json', {'trials': trials, 'selected': selected})
    write_json(args.report_dir / 'selection.json', {'trials': trials, 'selected': selected})
    selected_path = args.output_dir / selected['trial'] / 'best'
    model = AutoModelForSequenceClassification.from_pretrained(selected_path,
        local_files_only=True, dtype=torch.float32, attn_implementation='sdpa')
    evaluation_args = TrainingArguments(output_dir=str(args.output_dir / 'evaluation'),
        per_device_eval_batch_size=args.batch_size, use_cpu=args.device == 'cpu',
        dataloader_pin_memory=False, disable_tqdm=True, report_to='none')
    evaluator = Trainer(model=model, args=evaluation_args, processing_class=tokenizer,
                        data_collator=collator)
    predictions, metrics = {}, {}
    for split in ['dev', 'test', 'regression', 'train']:
        output = evaluator.predict(encoded[split], metric_key_prefix=split)
        rows = prediction_rows(splits[split], output)
        predictions[split] = rows
        metrics[split] = summarize(rows)
        metrics[split]['loss'] = float(output.metrics[f'{split}_loss'])
        metrics[split]['prediction_seconds'] = float(output.metrics[f'{split}_runtime'])
        if split == 'dev':
            if (abs(metrics[split]['macro_f1'] - selected['macro_f1']) > 1e-10
                    or abs(metrics[split]['loss'] - selected['loss']) > 1e-5):
                raise RuntimeError('reloaded checkpoint does not reproduce selected dev metrics')
        (args.report_dir / f'{split}-predictions.jsonl').write_text(
            ''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n' for row in rows), encoding='utf-8')
    for split, expected in manifest['data_sha256'].items():
        if digest(ROOT / 'data' / f'{split}.jsonl') != expected:
            raise RuntimeError('source data changed during the experiment')
    manifest['selected_checkpoint'] = f"{selected['trial']}/best"
    manifest['selected_model_sha256'] = digest(selected_path / 'model.safetensors')
    manifest['elapsed_seconds'] = time.perf_counter() - started
    manifest['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_json(args.output_dir / 'manifest.json', manifest)
    write_json(args.report_dir / 'manifest.json', manifest)
    write_json(args.report_dir / 'metrics.json', metrics)
    write_misclassifications(args.report_dir / 'misclassifications.md', predictions)
    print(json.dumps({'event': 'finished', 'selected': selected,
                      'results': {split: {'correct': value['correct'], 'n': value['n'],
                       'accuracy': value['accuracy'], 'macro_f1': value['macro_f1']} for split, value in metrics.items()}},
                     ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
