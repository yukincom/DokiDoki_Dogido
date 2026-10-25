"""CPU readout of saved Gemma 4 residuals; no model generation or raw-data writes."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

SCHEMA = 'dogido.j_lens.v1'
BOUNDARY = 'decoder_layer_output_pre_final_norm'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked(path, expected):
    if digest(path) != expected:
        raise ValueError('readout asset hash mismatch')


def top_candidates(x, head, dequantize, tokens, softcap, count=16, block_size=4096):
    """Keep only top-k; never materialize the entire float32 vocabulary head."""
    scores = np.empty((len(x), 0), np.float32)
    ids = np.empty((len(x), 0), np.int64)
    logsum = np.full(len(x), -np.inf, np.float64)
    for start in range(0, len(tokens), block_size):
        weights = dequantize(head.data[start:start + block_size], head.tensor_type)
        block = x @ weights.T
        block = softcap * np.tanh(block / softcap)
        maxima = block.max(axis=1).astype(np.float64)
        partial = maxima + np.log(np.exp(block.astype(np.float64) - maxima[:, None]).sum(axis=1))
        logsum = np.logaddexp(logsum, partial)
        take = min(count, block.shape[1])
        at = np.argpartition(block, -take, axis=1)[:, -take:]
        values = np.take_along_axis(block, at, axis=1)
        merged = np.concatenate([scores, values], axis=1)
        merged_ids = np.concatenate([ids, at + start], axis=1)
        top = np.argsort(-merged, axis=1, kind='stable')[:, :count]
        scores = np.take_along_axis(merged, top, axis=1)
        ids = np.take_along_axis(merged_ids, top, axis=1)
    if not np.isfinite(scores).all():
        raise ValueError('nonfinite readout')
    return [[{'id': int(k), 'piece': tokens[k], 'score': float(v),
              'logprob': float(v - logsum[row])} for k, v in zip(ids[row], scores[row])]
            for row in range(len(x))]


def readout(config, header, hidden):
    binding_path = Path(config['binding'])
    binding = json.loads(binding_path.read_text())
    if (binding['status'] != 'exploratory_transfer' or
        header['model_sha256'] != binding['model_sha256'] or
        header['boundary'] != BOUNDARY or header['backend'] != 'llama.cpp' or
        header['shape'][1:] != [30, 2816]):
        raise ValueError('capture/model/lens binding mismatch')
    checked(config['model'], binding['model_sha256'])
    checked(config['lens'], binding['lens_sha256'])
    # Use the same official GGUF reader revision as the collector, not a second decoder.
    gguf_root = Path(config['gguf_python'])
    for name, sha in binding['gguf_files'].items():
        checked(gguf_root / name, sha)
    sys.path.insert(0, str(gguf_root))
    from gguf import GGUFReader, dequantize
    reader = GGUFReader(config['model'])
    tensors = {tensor.name: tensor for tensor in reader.tensors}
    if reader.fields['general.architecture'].contents() != 'gemma4':
        raise ValueError('unsupported model architecture')
    head = tensors.get('output.weight', tensors['token_embd.weight'])
    norm = tensors['output_norm.weight'].data.copy()
    epsilon = float(reader.fields['gemma4.attention.layer_norm_rms_epsilon'].contents())
    softcap = float(reader.fields['gemma4.final_logit_softcapping'].contents())
    tokens = reader.fields['tokenizer.ggml.tokens'].contents()
    if hidden.shape != (len(header['positions']), 30, 2816) or not np.isfinite(hidden).all():
        raise ValueError('invalid selected residuals')
    projected = hidden.copy()
    with np.load(config['lens'], allow_pickle=False) as lens:
        if set(lens.files) != {f'layer_{n}' for n in range(29)}:
            raise ValueError('lens layer mismatch')
        for layer in range(29):
            matrix = lens[f'layer_{layer}']
            if matrix.shape != (2816, 2816) or not np.isfinite(matrix).all():
                raise ValueError('invalid lens matrix')
            projected[:, layer, :] = hidden[:, layer, :] @ matrix.T
    x = projected.reshape(-1, 2816)
    # GGUF stores the already adjusted norm weight; do not add HF's +1 again.
    x = x / np.sqrt(np.mean(x * x, axis=-1, keepdims=True) + epsilon) * norm
    candidates = top_candidates(x, head, dequantize, tokens, softcap)
    rows = []
    for i, position in enumerate(header['positions']):
        token_id = position['predicted_token_id']
        rows.append({**position, 'predicted_piece': tokens[token_id], 'layers': [
            {'layer': layer, 'method': 'native_final_head' if layer == 29 else 'transferred_j_lens',
             'candidates': candidates[i * 30 + layer]} for layer in range(30)]})
    return {'schema': SCHEMA, **header, 'status': 'exploratory_transfer',
            'processor_sha256': digest(__file__), 'binding_sha256': digest(binding_path),
            'lens_sha256': binding['lens_sha256'], 'positions': rows,
            'token_format': 'gguf_token_piece', 'score_kind': 'softcapped_logit_before_sampling',
            'native_head_validation': binding['native_head_validation'],
            'numeric_implementation': {'numpy': np.__version__, 'dtype': 'float32',
                'gguf_source_revision': binding['collector_source_revision'],
                'output_head': head.name, 'rms_epsilon': epsilon, 'logit_softcap': softcap}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    header = json.loads(sys.stdin.buffer.readline(1_000_000))
    count = len(header['positions'])
    if count < 1 or count > 3:
        raise ValueError('only before/middle/after positions are supported')
    size = count * 30 * 2816 * 4
    payload = sys.stdin.buffer.read(size + 1)
    if len(payload) != size:
        raise ValueError('readout residual size mismatch')
    hidden = np.frombuffer(payload, dtype='<f4').reshape(count, 30, 2816)
    print(json.dumps(readout(config, header, hidden), ensure_ascii=False, separators=(',', ':'), allow_nan=False))


if __name__ == '__main__':
    main()
