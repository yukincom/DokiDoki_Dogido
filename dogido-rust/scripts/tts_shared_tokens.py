"""One lazy UniDic instance shared by token operations in each Rust-owned worker."""
from tts_unidic_adapter import Unidic, handle as token_response

_reader = Unidic()


def handle(frame):
    if not isinstance(frame, dict) or frame.get('op') != 'tts_tokens':
        raise ValueError('invalid shared token request')
    request = {key: value for key, value in frame.items() if key != 'op'}
    return token_response(request, _reader)
