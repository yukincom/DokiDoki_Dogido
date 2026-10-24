"""native decoderの計算結果をそのまま返し、所有する層の出力だけを観測へ渡す。"""
from contextlib import contextmanager


@contextmanager
def observe_decoder_outputs(core, decoder_class, on_output):
    """層番号とnative結果をcallbackへ渡す。設定と復元は一つの生成threadが所有する。

別モデルの層は対象外とし、例外でも元の呼出しを復元する。
    """
    owners = {id(layer): index for index, layer in enumerate(core.layers)}
    original = decoder_class.__call__

    def observed(layer, *args, **kwargs):
        result = original(layer, *args, **kwargs)
        index = owners.get(id(layer))
        if index is not None:
            on_output(index, result)
        return result

    decoder_class.__call__ = observed
    try:
        yield
    finally:
        decoder_class.__call__ = original
