#!/usr/bin/env python3
"""Launch the native text workshop using existing local model settings."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.config import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=5057)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--resume', type=Path)
    args = parser.parse_args()
    os.chdir(args.settings_dir)
    settings = get_settings()
    base = settings.llm_chat_base_url or settings.llm_base_url or 'http://127.0.0.1:8080/v1'
    model = settings.llm_chat_model or settings.llm_model or 'default_model'
    if urlsplit(base).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        parser.error('既存のローカルモデルを指定してください。')
    binary = ROOT / 'dogido-rust/target/release/examples/workshop_text'
    memory = ROOT / '.dogido_memory/rust-migration'
    if not binary.is_file() or not (memory / 'sessions').is_dir():
        parser.error('Rust本体のビルドと保存句が必要です。')
    print(f'川柳の相談室: http://127.0.0.1:{args.port} / モデル: {model}', flush=True)
    if args.check:
        return
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    key = settings.llm_chat_api_key or settings.llm_api_key
    if key:
        env['DOGIDO_LLM_API_KEY'] = key
    else:
        env.pop('DOGIDO_LLM_API_KEY', None)
    haiku_base = settings.llm_haiku_base_url or settings.llm_base_url or base
    haiku_model = settings.llm_haiku_model or settings.llm_model or model
    if urlsplit(haiku_base).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        parser.error('修正生成にも既存のローカルモデルを指定してください。')
    haiku_key = settings.llm_haiku_api_key or settings.llm_api_key
    if haiku_key:
        env['DOGIDO_LLM_HAIKU_API_KEY'] = haiku_key
    else:
        env.pop('DOGIDO_LLM_HAIKU_API_KEY', None)
    haiku = {'base_url': haiku_base, 'model': haiku_model,
             'max_tokens': settings.llm_haiku_max_tokens or settings.llm_max_tokens,
             'timeout_ms': int(1000 * (settings.llm_haiku_timeout_sec or settings.llm_timeout_sec)),
             'generation_strategy': settings.haiku_generation_strategy,
             'max_regeneration_rounds': settings.haiku_max_regeneration_rounds}
    os.chdir(ROOT)
    command = [str(binary), '--listen', f'127.0.0.1:{args.port}',
                      '--memory-dir', str(memory), '--python', sys.executable,
                      '--model', model, '--base-url', base,
                      '--max-tokens', str(settings.llm_chat_max_tokens or settings.llm_max_tokens),
                      '--timeout-ms', str(int(1000 * (settings.llm_chat_timeout_sec or settings.llm_timeout_sec))),
                      '--haiku-settings', json.dumps(haiku)]
    if args.resume:
        command.extend(['--resume', str(args.resume.resolve())])
    os.execve(binary, command, env)


if __name__ == '__main__':
    main()
