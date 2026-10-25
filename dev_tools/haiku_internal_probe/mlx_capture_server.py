"""mlx-lmの既存サーバーへ、明示要求だけの内部表現採取を接続する。

token化・cache・sampling・生成・HTTP整形はnative実装が担当する。
採取要求は既存の単一要求処理を使う。MLXの先読み方式は採取未対応として記録する。
既存のpriority wrapperも入口として利用できる。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import importlib.metadata
import inspect
import json
import logging
import os
from pathlib import Path
import runpy
import sys
import threading

from capture_store import CaptureStore
from native_decoder_capture import observe_decoder_outputs
from mlx_capture_identity import prepare_identity, template_identity, verify_identity


class Session:
    """一応答の先読みframeと保存状態を保持し、HTTP完了前に採取結果を確定する。"""
    def __init__(self, store):
        self.store = store
        self.frames = {}
        self.current = None
        self.text = []
        self.producer_done = False
        self.context = None
        self.encoder = None

    def safe(self, operation, *args):
        try:
            return operation(*args)
        except Exception as error:
            self.store.fail(f"{type(error).__name__}: {error}")
            logging.warning("Internal capture failed for %s (%s)",
                            self.store.record["response_id"], type(error).__name__)
            self.frames.clear()
            self.current = None
            return None

    def token(self, token):
        with self.store.lock:
            index = self.store.record["total_generated_tokens"]
            position = len(self.store.record["prompt_token_ids"]) + index - 1
            frame = self.frames.pop(position, None)
            encoded = None
            if self.store.wants_frame:
                if frame is None:
                    self.store.fail("missing native predictive frame")
                else:
                    encoded = self.safe(self.encoder, frame)
            self.safe(self.store.append_token, int(token), encoded, position)

    def rendered(self, text, finish_reason):
        with self.store.lock:
            self.text.append(text or "")
            if finish_reason is not None:
                if not self.producer_done:
                    self.store.fail("generation ended without producer completion")
                cancelled = self.context is not None and self.context._should_stop
                self.safe(self.store.finish, "".join(self.text), finish_reason,
                          "cancelled" if cancelled else None)

    def end_http(self, error=None):
        with self.store.lock:
            if not self.store.finished:
                if error is not None and not isinstance(error, (BrokenPipeError, ConnectionResetError)):
                    self.store.fail(f"HTTP generation failed: {type(error).__name__}")
                self.safe(self.store.finish, "".join(self.text), None, "cancelled")


class RecordingQueue:
    """Observe native responses before stop/control-token text processing."""
    def __init__(self, queue, session, server):
        self.queue, self.session, self.server = queue, session, server

    def put(self, item, *args, **kwargs):
        if isinstance(item, self.server.GenerationContext):
            self.session.context = item
            self.session.safe(self.session.store.set_prompt, item.prompt)
        elif isinstance(item, self.server.Response):
            self.session.safe(self.session.token, item.token)
        elif isinstance(item, Exception):
            self.session.store.fail(f"native generation failed: {type(item).__name__}")
        elif item is None:
            self.session.producer_done = True
            self.session.frames.clear()
        return self.queue.put(item, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.queue, name)


def cache_position(inputs, cache, layers):
    """Use logical cache offsets, including sliding caches, never storage length."""
    if len(inputs.shape) != 2 or inputs.shape[0] != 1 or inputs.shape[1] < 1:
        raise ValueError("only a nonempty token batch of one is supported")
    if cache is None or len(cache) != layers:
        raise ValueError("capture requires the native per-layer prompt cache")
    offsets = [getattr(value, "offset", None) for value in cache]
    if any(type(offset) is not int or offset < 0 for offset in offsets):
        raise ValueError("unsupported native cache offset")
    if len(set(offsets)) != 1:
        raise ValueError("per-layer cache offsets differ")
    return offsets[0] + inputs.shape[1] - 1


@contextmanager
def observe_model(model, core, decoder_class, session):
    """Retain predictive frames by input position across native MLX lookahead."""
    model_class = type(model)
    original = model_class.__call__

    def layer_output(index, result):
        if session.current is None or session.store.error is not None:
            return
        def save():
            hidden = result[0]
            if len(hidden.shape) != 3 or hidden.shape[0] != 1 or hidden.shape[2] != session.store.width:
                raise ValueError("unexpected native decoder output shape")
            session.current[1][index] = hidden[0, -1, :]
        session.safe(save)

    def observed(instance, *args, **kwargs):
        if instance is not model or not session.store.wants_frame:
            return original(instance, *args, **kwargs)
        def begin():
            inputs = args[0] if args else kwargs.get("inputs")
            cache = kwargs.get("cache", args[1] if len(args) > 1 else None)
            if kwargs.get("input_embeddings") is not None or kwargs.get("per_layer_inputs") is not None:
                raise ValueError("embedding-input capture is unsupported")
            position = cache_position(inputs, cache, session.store.layers)
            first = len(session.store.record["prompt_token_ids"]) - 1
            if first < 0:
                raise ValueError("native prompt was not observed")
            if position >= first and (session.store.max_positions is None or
                                      position < first + session.store.max_positions):
                session.current = (position, {})
        session.safe(begin)
        try:
            result = original(instance, *args, **kwargs)
            if session.current is not None:
                def finish():
                    position, values = session.current
                    if sorted(values) != list(range(session.store.layers)):
                        raise ValueError("native decoder layer outputs are incomplete")
                    if position in session.frames:
                        raise ValueError("duplicate native predictive position")
                    # Native generate_step keeps at most one token of lookahead.
                    if len(session.frames) >= 3:
                        raise ValueError("native lookahead exceeds supported bound")
                    session.frames[position] = [values[i] for i in range(session.store.layers)]
                session.safe(finish)
            return result
        finally:
            session.current = None

    model_class.__call__ = observed
    try:
        with observe_decoder_outputs(core, decoder_class, layer_output):
            yield
    finally:
        model_class.__call__ = original


def native_binding(model, provider, server, declared_identity=None):
    """Only the reviewed Gemma4 26B native boundary is supported initially."""
    import mlx.core as mx
    import numpy as np
    from mlx_lm.models import gemma4, gemma4_text
    from mlx_lm import generate as generate_function

    assets = verify_identity(declared_identity, provider.model_key)

    if type(model) is gemma4.Model:
        core = model.language_model.model
    elif type(model) is gemma4_text.Model:
        core = model.model
    else:
        raise ValueError("unsupported model class for internal capture")
    cfg = core.config
    if (cfg.num_hidden_layers, cfg.hidden_size, cfg.num_kv_shared_layers,
            cfg.hidden_size_per_layer_input) != (30, 2816, 0, 0) or not cfg.enable_moe_block:
        raise ValueError("unsupported Gemma4 configuration for internal capture")
    if type(core) is not gemma4_text.Gemma4TextModel or any(
            type(layer) is not gemma4_text.DecoderLayer for layer in core.layers):
        raise ValueError("unsupported decoder implementation")
    layer_source = inspect.getsource(gemma4_text.DecoderLayer.__call__)
    core_source = inspect.getsource(type(core).__call__)
    if "return h, shared_kv, offset" not in layer_source or "return self.norm(h)" not in core_source:
        raise ValueError("native decoder output boundary changed")
    sources = {Path(inspect.getsourcefile(obj)) for obj in
        [server, generate_function, gemma4, gemma4_text, observe_decoder_outputs, prepare_identity]}
    sources.update({Path(__file__), Path(inspect.getsourcefile(CaptureStore))})
    identity = {
        "model_key": str(provider.model_key),
        "model_class": f"{type(model).__module__}.{type(model).__name__}",
        "config": asdict(cfg), "mlx_lm_version": importlib.metadata.version("mlx-lm"),
        "assets": assets, "active_chat_template": template_identity(provider.tokenizer),
        "implementation_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in sorted(sources)},
    }
    def encode(values):
        hidden = mx.stack(values).astype(mx.float32)
        mx.eval(hidden)
        return np.asarray(hidden, dtype="<f4").tobytes(order="C")
    return core, gemma4_text.DecoderLayer, identity, encode


@contextmanager
def install(server, environ=None, binding=native_binding, model_identity=None):
    """Patch base methods before an optional upstream priority wrapper installs."""
    environ = os.environ if environ is None else environ
    root = environ.get("DOGIDO_CAPTURE_DIR")
    limit = int(environ.get("DOGIDO_CAPTURE_MAX_POSITIONS", "256"))
    if not 1 <= limit <= 4096:
        raise ValueError("DOGIDO_CAPTURE_MAX_POSITIONS must be 1..4096")
    identity = model_identity
    if identity is None and environ.get("DOGIDO_CAPTURE_IDENTITY"):
        declared = json.loads(Path(environ["DOGIDO_CAPTURE_IDENTITY"]).read_text())
        identity = prepare_identity(declared["resolved_model_path"])
        if identity != declared:
            raise ValueError("supplied MLX asset identity does not match current files")
    registry, lock, generation_lock = {}, threading.RLock(), threading.RLock()
    handler, generator = server.APIHandler, server.ResponseGenerator
    originals = {"handle": handler.handle_completion, "response": handler.generate_response,
        "next": generator._next_request, "batch": generator._is_batchable,
        "single": generator._serve_single}

    def handle(self, request, stop_words):
        session = None
        if root and self.body.get("dogido_capture") is True:
            try:
                request_limit = None if self.body.get("dogido_capture_full") is True else limit
                session = Session(CaptureStore(root, self.request_id, "mlx-lm", request_limit))
                with lock:
                    registry[self.request_id] = session
                request._dogido_capture_id = self.request_id
                self._dogido_capture_session = session
            except Exception as error:
                logging.warning("Cannot create internal capture (%s)", type(error).__name__)
        error = None
        try:
            return originals["handle"](self, request, stop_words)
        except BaseException as caught:
            error = caught
            raise
        finally:
            if session is not None:
                session.end_http(error)
                with lock:
                    registry.pop(self.request_id, None)
                self._dogido_capture_session = None

    def response(self, text, finish_reason, *args, **kwargs):
        session = getattr(self, "_dogido_capture_session", None)
        if session is not None:
            session.safe(session.rendered, text, finish_reason)
        return originals["response"](self, text, finish_reason, *args, **kwargs)

    def next_request(self, *args, **kwargs):
        item = originals["next"](self, *args, **kwargs)
        if item is not None:
            _, request, generation_args = item
            capture_id = getattr(request, "_dogido_capture_id", None)
            if capture_id is not None:
                generation_args._dogido_capture_id = capture_id
        return item

    def batchable(self, args):
        if getattr(args, "_dogido_capture_id", None) is not None:
            return False
        return originals["batch"](self, args)

    def single(self, item):
        queue, request, args = item
        with lock:
            session = registry.get(getattr(request, "_dogido_capture_id", None))
        if session is None:
            return originals["single"](self, item)
        recording_queue = RecordingQueue(queue, session, server)
        native_item = (recording_queue, request, args)
        with generation_lock:
            try:
                if getattr(self, "_is_distributed", False):
                    raise ValueError("distributed capture is unsupported")
                if self.model_provider.draft_model is not None:
                    raise ValueError("speculative capture is unsupported")
                model = self.model_provider.model
                core, decoder, model_identity, encoder = binding(model, self.model_provider, server, identity)
                session.store.configure(len(core.layers), core.config.hidden_size, model_identity)
                session.encoder = encoder
            except Exception as error:
                session.store.fail(f"{type(error).__name__}: {error}")
                return originals["single"](self, native_item)
            try:
                with observe_model(model, core, decoder, session):
                    return originals["single"](self, native_item)
            except BaseException as error:
                session.store.fail(f"native generation interrupted: {type(error).__name__}")
                raise
            finally:
                session.frames.clear()

    handler.handle_completion, handler.generate_response = handle, response
    generator._next_request, generator._is_batchable = next_request, batchable
    generator._serve_single = single
    try:
        yield
    finally:
        handler.handle_completion, handler.generate_response = originals["handle"], originals["response"]
        generator._next_request, generator._is_batchable = originals["next"], originals["batch"]
        generator._serve_single = originals["single"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-entry", type=Path,
                        help="Existing Python server entrypoint (for example a priority wrapper)")
    parser.add_argument("--identity-model", type=Path,
                        help="Local MLX model directory to fingerprint before the native server loads it")
    parser.add_argument("server_args", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    forwarded = options.server_args
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    identity = prepare_identity(options.identity_model) if options.identity_model else None
    from mlx_lm import server
    with install(server, model_identity=identity):
        if options.upstream_entry:
            entry = options.upstream_entry.resolve(strict=True)
            sys.path.insert(0, str(entry.parent))
            sys.argv = [str(entry), *forwarded]
            runpy.run_path(str(entry), run_name="__main__")
        else:
            sys.argv = ["mlx_lm.server", *forwarded]
            server.main()


if __name__ == "__main__":
    main()
