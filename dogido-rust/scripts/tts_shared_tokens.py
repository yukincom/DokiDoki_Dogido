"""Token projection for an existing helper; shares its canonical UniDic singleton.

No new process, Tagger, warmup, reading policy, or catalog overlay is introduced.
The neutral poem-reading path retains ownership of its existing dictionary cache.
"""
from tts_unidic_adapter import Unidic, handle as token_response


class SharedUnidic(Unidic):
    def initialize(self):
        # Importing the module is lazy and does not initialize the optional SDK.
        # Both neutral poem transforms and free-text TTS use this same getter.
        from dogido_server.tts_reading import _get_unidic_tagger
        return _get_unidic_tagger()


_reader = SharedUnidic()


def handle(frame):
    if not isinstance(frame, dict) or frame.get('op') != 'tts_tokens':
        raise ValueError('invalid shared token request')
    request = {key: value for key, value in frame.items() if key != 'op'}
    # The underlying strict wire validator runs before dictionary initialization.
    return token_response(request, _reader)
