"""Readout math and binding tests; no generation, model load, or network."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import numpy as np
from dev_tools.haiku_internal_probe.gguf_jlens import top_candidates, readout


class ReadoutTests(unittest.TestCase):
    def test_blocked_vocabulary_matches_dense_head_and_global_logprob(self):
        rng = np.random.default_rng(42)
        weights = rng.normal(size=(37, 6)).astype(np.float32)
        residuals = rng.normal(size=(3, 6)).astype(np.float32)
        tokens = [f'▁<{n}>' for n in range(37)]
        rows = top_candidates(residuals, SimpleNamespace(data=weights, tensor_type=None),
                              lambda block, _: block, tokens, 30, count=5, block_size=7)
        logits = 30 * np.tanh(residuals @ weights.T / 30)
        for at, row in enumerate(rows):
            expected = np.argsort(-logits[at])[:5]
            self.assertEqual([x['id'] for x in row], expected.tolist())
            self.assertEqual([x['piece'] for x in row], [tokens[n] for n in expected])
            denom = np.log(np.exp(logits[at].astype(np.float64)).sum())
            np.testing.assert_allclose([x['score'] for x in row], logits[at, expected], atol=1e-6)
            np.testing.assert_allclose([x['logprob'] for x in row], logits[at, expected]-denom, atol=1e-6)

    def test_wrong_checkpoint_or_boundary_never_loads_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'binding.json'
            path.write_text(json.dumps({'status':'exploratory_transfer','model_sha256':'expected'}))
            for header in [{'model_sha256':'different'},
                           {'model_sha256':'expected','boundary':'post_norm'},
                           {'model_sha256':'expected','boundary':'decoder_layer_output_pre_final_norm',
                            'backend':'mlx'}]:
                with self.assertRaisesRegex(ValueError, 'binding mismatch'):
                    readout({'binding':str(path)}, header, None)


if __name__ == '__main__':
    unittest.main()
