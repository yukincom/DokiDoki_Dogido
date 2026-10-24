"""実モデルを使わず、採取保存とnativeサーバーの観測hookの契約を検査する。"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from queue import Queue
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from capture_store import CaptureStore
from mlx_capture_server import cache_position, install
from native_decoder_capture import observe_decoder_outputs
from mlx_capture_identity import prepare_identity, template_identity, verify_identity


def floats(values):
    return struct.pack("<" + "f" * len(values), *values)


class IdentityTests(unittest.TestCase):
    def test_local_assets_hashes_and_native_binding(self):
        with TemporaryDirectory() as root:
            directory = Path(root)
            for name, content in {"model.safetensors": b"weights", "config.json": b"{}",
                                  "tokenizer.json": b"{}", "chat_template.jinja": b"template"}.items():
                (directory / name).write_bytes(content)
            def fingerprint(path):
                stat = path.stat()
                return {"path": str(path.resolve()), "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            identity = prepare_identity(directory, fingerprint)
            self.assertEqual(set(identity["files"]), {"model.safetensors", "config.json", "tokenizer.json", "chat_template.jinja"})
            self.assertIs(verify_identity(identity, (root, None, None)), identity)
            with self.assertRaises(ValueError):
                verify_identity(None, (root, None, None))
            with self.assertRaises(ValueError):
                verify_identity(identity, (root, "adapter", None))
            (directory / "tokenizer.json").write_text('{"changed":true}')
            with self.assertRaises(ValueError):
                verify_identity(identity, (root, None, None))
            self.assertNotEqual(template_identity(SimpleNamespace(chat_template="a")),
                                template_identity(SimpleNamespace(chat_template="b")))


class StorageTests(unittest.TestCase):
    def test_shape_hash_alignment_and_cap(self):
        with TemporaryDirectory() as root:
            store = CaptureStore(root, "chatcmpl-example", "mlx-lm", 1)
            store.configure(2, 2, {"name": "fake"})
            store.set_prompt([7, 8, 9])
            store.append_token(10, floats([1, 2, 3, 4]), 2)
            store.append_token(11)
            store.finish("本文", "length")
            record = json.loads((store.directory / "manifest.json").read_text())
            data = (store.directory / "hidden.f32").read_bytes()
            self.assertEqual(record["shape"], [1, 2, 2])
            self.assertEqual(record["status"], "partial")
            self.assertEqual(record["total_generated_tokens"], 2)
            self.assertEqual(record["predicted_token_ids"], [10])
            self.assertEqual(record["positions"], [{"generation_index": 0,
                "input_position": 2, "predicted_token_id": 10}])
            self.assertEqual(record["hidden_sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(record["hidden_bytes"], len(data))
            self.assertEqual(record["rendered_text"], "本文")
            with self.assertRaises(FileExistsError):
                CaptureStore(root, "chatcmpl-example", "mlx-lm")

    def test_nonfinite_and_misalignment_fail_without_a_row(self):
        for payload, position in [(floats([float("nan")]), 0), (floats([1]), 1)]:
            with self.subTest(position=position), TemporaryDirectory() as root:
                store = CaptureStore(root, "bad", "mlx-lm")
                store.configure(1, 1, {})
                store.set_prompt([1])
                with self.assertRaises(ValueError) as failure:
                    store.append_token(2, payload, position)
                store.fail(str(failure.exception))
                store.finish()
                self.assertEqual(store.record["status"], "failed")
                self.assertEqual(store.record["shape"], [0, 1, 1])

    def test_exact_cap_is_complete_and_cancel_is_explicit(self):
        with TemporaryDirectory() as root:
            store = CaptureStore(root, "exact", "mlx-lm", 1)
            store.configure(1, 1, {})
            store.set_prompt([1])
            store.append_token(2, floats([3]), 0)
            store.finish("a", "stop")
            self.assertEqual(store.record["status"], "complete")
            cancelled = CaptureStore(root, "cancelled", "mlx-lm")
            cancelled.finish(status="cancelled")
            self.assertEqual(cancelled.record["status"], "cancelled")

    def test_ids_cannot_escape_operator_root(self):
        with TemporaryDirectory() as root:
            for name in ["../escape", "/absolute", "two/parts", "", "a" * 129]:
                with self.assertRaises(ValueError):
                    CaptureStore(root, name, "mlx-lm")


class Input:
    def __init__(self, tokens, batch=1):
        self.tokens = tokens
        self.shape = (batch, len(tokens))


class Hidden:
    def __init__(self, value, length):
        self.value = value
        self.shape = (1, length, 2)

    def __getitem__(self, key):
        return [self.value, self.value + 0.5]


class Layer:
    def __init__(self, index):
        self.index = index

    def __call__(self, value, length):
        return Hidden(value * 10 + self.index, length), None, None


class Model:
    def __init__(self):
        self.core = SimpleNamespace(layers=[Layer(0), Layer(1)], config=SimpleNamespace(hidden_size=2))
        self.calls = 0

    def __call__(self, inputs, cache=None):
        self.calls += 1
        position = cache[0].offset + inputs.shape[1] - 1
        for layer in self.core.layers:
            layer(position, inputs.shape[1])
        for slot in cache:
            slot.offset += inputs.shape[1]
        return position


@dataclass
class Context:
    prompt: list
    _should_stop: bool = False


@dataclass
class Response:
    token: int
    text: str
    finish_reason: str | None = None


class Generator:
    def __init__(self, model, cached=0, cancel=False):
        self.model_provider = SimpleNamespace(model=model, draft_model=None, model_key="fake")
        self.cached = cached
        self.cancel = cancel
        self._is_distributed = False
        self.requests = Queue()

    def _next_request(self):
        return self.requests.get_nowait()

    def _is_batchable(self, args):
        return True

    def _serve_single(self, item):
        queue, request, args = item
        prompt = [1, 2, 3, 4, 5]
        context = Context(prompt)
        queue.put(context)
        cache = [SimpleNamespace(offset=self.cached) for _ in range(2)]
        model = self.model_provider.model
        # Native prefill chunks before the final input must not become rows.
        if not self.cached:
            model(Input(prompt[:3]), cache=cache)
        remainder = prompt[cache[0].offset:]
        model(Input(remainder), cache=cache)
        # Match mlx-lm's one-token lookahead: model(token) precedes yield token.
        for index, token in enumerate([7, 8, 0]):
            model(Input([token]), cache=cache)
            queue.put(Response(token, "" if token == 0 else str(token), "stop" if token == 0 else None))
            if self.cancel:
                context._should_stop = True
                break
        queue.put(None)


class Handler:
    def __init__(self, generator, body=None, stream=False, root=None, disconnected=False):
        self.body = body or {}
        self.request_id = "chatcmpl-test"
        self.response_generator = generator
        self.stream = stream
        self.root = root
        self.disconnected = disconnected

    def handle_completion(self, request, stop_words):
        args = SimpleNamespace()
        queue = Queue()
        self.response_generator.requests.put((queue, request, args))
        item = self.response_generator._next_request()
        self.batchable = self.response_generator._is_batchable(args)
        self.response_generator._serve_single(item)
        text = ""
        while True:
            response = queue.get_nowait()
            if response is None:
                break
            if isinstance(response, Context):
                continue
            if self.disconnected:
                raise BrokenPipeError("disconnected")
            if self.stream:
                self.generate_response(response.text, None)
            else:
                text += response.text
        return self.generate_response(text, "stop")

    def generate_response(self, text, finish_reason):
        if finish_reason is not None and self.body.get("dogido_capture") is True and self.root:
            # The sidecar must be visible before the native final response.
            self.final_manifest = json.loads((Path(self.root) / self.request_id / "manifest.json").read_text())
        return {"text": text, "finish_reason": finish_reason}


def fake_binding(model, provider, server, identity):
    return model.core, Layer, {"name": "fake"}, lambda rows: floats([v for row in rows for v in row])


class HookTests(unittest.TestCase):
    def run_request(self, root, body=None, cached=0, stream=False, draft=False,
                    binding=fake_binding, cap=256, cancel=False, disconnected=False):
        server = SimpleNamespace(APIHandler=Handler, ResponseGenerator=Generator,
            GenerationContext=Context, Response=Response)
        model = Model()
        generator = Generator(model, cached, cancel)
        if draft:
            generator.model_provider.draft_model = object()
        handler = Handler(generator, body, stream, root, disconnected)
        original_model, original_layer = Model.__call__, Layer.__call__
        original_handler, original_single = Handler.handle_completion, Generator._serve_single
        with install(server, {"DOGIDO_CAPTURE_DIR": root,
                              "DOGIDO_CAPTURE_MAX_POSITIONS": str(cap)}, binding):
            if disconnected:
                with self.assertRaises(BrokenPipeError):
                    handler.handle_completion(SimpleNamespace(), [])
            else:
                handler.handle_completion(SimpleNamespace(), [])
        self.assertIs(Model.__call__, original_model)
        self.assertIs(Layer.__call__, original_layer)
        self.assertIs(Handler.handle_completion, original_handler)
        self.assertIs(Generator._serve_single, original_single)
        path = Path(root) / "chatcmpl-test" / "manifest.json"
        return handler, model, json.loads(path.read_text()) if path.exists() else None

    def test_native_lookahead_cache_and_eos_association(self):
        for cached in [0, 3, 4]:
            with self.subTest(cached=cached), TemporaryDirectory() as root:
                handler, model, record = self.run_request(root, {"dogido_capture": True}, cached)
                self.assertFalse(handler.batchable)
                self.assertEqual(record["status"], "complete")
                self.assertEqual(record["prompt_token_ids"], [1, 2, 3, 4, 5])
                self.assertEqual(record["predicted_token_ids"], [7, 8, 0])
                self.assertEqual([row["input_position"] for row in record["positions"]], [4, 5, 6])
                values = struct.unpack("<12f", (Path(root) / "chatcmpl-test" / "hidden.f32").read_bytes())
                self.assertEqual(values, (40, 40.5, 41, 41.5, 50, 50.5, 51, 51.5, 60, 60.5, 61, 61.5))
                self.assertEqual(record["rendered_text"], "78")
                self.assertEqual(model.calls, 5 if cached == 0 else 4)

    def test_strict_opt_in_and_no_new_model_calls(self):
        for body in [{}, {"dogido_capture": False}, {"dogido_capture": "true"}, {"dogido_capture": 1}]:
            with self.subTest(body=body), TemporaryDirectory() as root:
                handler, model, record = self.run_request(root, body)
                self.assertTrue(handler.batchable)
                self.assertIsNone(record)
                self.assertEqual(model.calls, 5)
                self.assertEqual(list(Path(root).iterdir()), [])

    def test_streaming_and_cap_keep_prefix_and_native_generation(self):
        with TemporaryDirectory() as root:
            _, model, record = self.run_request(root, {"dogido_capture": True}, stream=True, cap=1)
            self.assertEqual(record["status"], "partial")
            self.assertEqual(record["total_generated_tokens"], 3)
            self.assertEqual(record["predicted_token_ids"], [7])
            self.assertEqual(record["shape"], [1, 2, 2])
            self.assertEqual(record["rendered_text"], "78")
            self.assertEqual(model.calls, 5)

    def test_unsupported_models_and_drafts_explicitly_fail_without_changing_generation(self):
        def unsupported(*args):
            raise ValueError("unsupported model")
        for draft, binding in [(True, fake_binding), (False, unsupported)]:
            with self.subTest(draft=draft), TemporaryDirectory() as root:
                _, model, record = self.run_request(root, {"dogido_capture": True}, draft=draft, binding=binding)
                self.assertEqual(record["status"], "failed")
                self.assertIn("unsupported", record["error"])
                self.assertEqual(record["total_generated_tokens"], 3)
                self.assertEqual(record["shape"], [0, 0, 0])
                self.assertEqual(record["rendered_text"], "78")
                self.assertEqual(model.calls, 5)

    def test_cancellation_and_disconnect(self):
        for cancel, disconnected in [(True, False), (False, True)]:
            with self.subTest(cancel=cancel), TemporaryDirectory() as root:
                _, _, record = self.run_request(root, {"dogido_capture": True},
                                               cancel=cancel, disconnected=disconnected)
                self.assertEqual(record["status"], "cancelled")

    def test_observer_filters_owned_layers_and_restores_on_error(self):
        core = Model().core
        foreign = Layer(8)
        seen = []
        original = Layer.__call__
        with self.assertRaises(RuntimeError):
            with observe_decoder_outputs(core, Layer, lambda index, result: seen.append(index)):
                core.layers[0](4, 1)
                foreign(4, 1)
                raise RuntimeError("interrupt")
        self.assertEqual(seen, [0])
        self.assertIs(Layer.__call__, original)

    def test_cache_validation_rejects_batches_unknown_offsets_and_misalignment(self):
        for inputs, cache in [(Input([1], 2), [SimpleNamespace(offset=0)]),
                              (Input([1]), [SimpleNamespace(offset=None)]),
                              (Input([1]), [SimpleNamespace(offset=1), SimpleNamespace(offset=2)])]:
            with self.assertRaises(ValueError):
                cache_position(inputs, cache, len(cache))


if __name__ == "__main__":
    unittest.main()
