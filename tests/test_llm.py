from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx

from dogido_server.config import Settings
from dogido_server.llm import DogidoLLM, LeafGenerationRequest, StructuredGenerationRequest
from dogido_server.llm.router import DogidoLLMRouter


class LLMTests(unittest.TestCase):
    def setUp(self) -> None:
        self.llm = DogidoLLM(Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop"))

    def test_clean_output_strips_think_block(self) -> None:
        text = "<think>\ninternal\n</think>\n\nあー……こわかった……"

        cleaned = self.llm._clean_output(text)

        self.assertEqual(cleaned, "あー……こわかった……")

    def test_router_reports_disabled_configuration(self) -> None:
        router = DogidoLLMRouter(Settings(audio_enabled=False, llm_enabled=False))

        self.assertFalse(router.enabled())
        self.assertFalse(router.route_enabled("haiku"))

    def test_clean_output_prefers_final_japanese_line_after_english_reasoning(self) -> None:
        text = (
            "Here's a thinking process:\n"
            "1. **Analyze User Input:**\n"
            "- **Role:** Minecraft AI\n"
            "Final answer: プレイヤーちゃん、ほんまに行くん？ ちょっと待ってや……。"
        )

        cleaned = self.llm._clean_output(text)

        self.assertEqual(cleaned, "プレイヤーちゃん、ほんまに行くん？ ちょっと待ってや……。")

    def test_clean_output_removes_harmless_dogido_role_label(self) -> None:
        self.assertEqual(
            self.llm._clean_output("ドギド: 今日は静かでええな。"),
            "今日は静かでええな。",
        )

    def test_clean_output_preserves_leading_quote_when_sentence_continues(self) -> None:
        self.assertEqual(
            self.llm._clean_output("「ひつじがあるく」は、春の風の情景や。"),
            "「ひつじがあるく」は、春の風の情景や。",
        )
        self.assertEqual(
            self.llm._clean_output("「返答全体や。」"),
            "返答全体や。",
        )

    def test_short_or_meta_output_is_rejected(self) -> None:
        self.assertFalse(self.llm._is_usable_output("ドギド"))
        self.assertFalse(self.llm._is_usable_output("例1: こわい"))
        self.assertFalse(self.llm._is_usable_output("user: こわい desu"))
        self.assertFalse(self.llm._is_usable_output("ふぁぁぁぁぁ"))
        self.assertTrue(self.llm._is_usable_output("あー……こわかった……"))
        self.assertTrue(self.llm._is_usable_output("すみません"))
        self.assertTrue(self.llm._is_usable_output("すみません、取り違えたわ。"))
        self.assertTrue(self.llm._is_usable_output("例を挙げるなら、松明やな。"))
        self.assertTrue(self.llm._is_usable_output("本番はちょっと緊張するな。"))
        self.assertTrue(self.llm._is_usable_output("ドギドの鼻も頼りになるやろ。"))
        self.assertTrue(self.llm._is_usable_output("Yukinちゃん、ほんまに行くん？", {"player_name": "Yukinちゃん"}))
        self.assertFalse(self.llm._is_usable_output("Yukinちゃん、ほんまに行くん？"))

    def test_live_player_chat_self_reference_is_accepted_without_repair(self) -> None:
        live_reply = (
            "パッと見は匂わへんけど、ドギドの鼻は鋭いねん。"
            "安心する匂いなら、それだけで十分やろ？"
        )

        class PlayerChatLLM(DogidoLLM):
            def __init__(self, settings):  # type: ignore[no-untyped-def]
                super().__init__(settings)
                self.calls = 0

            def enabled(self) -> bool:
                return True

            def _generate_backend_text(self, request):  # type: ignore[override]
                self.calls += 1
                return live_reply

        llm = PlayerChatLLM(
            Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop")
        )
        text = llm.generate_leaf_text(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="ようわからん、もうちょい教えて。",
                details={"user_text": "どうにおいが感じるって"},
            )
        )

        self.assertEqual(text, live_reply)
        self.assertEqual(llm.calls, 1)
        self.assertTrue(
            llm._is_style_acceptable(
                "player_chat",
                "オレにはさっぱりわからんけど、プレイヤーが匂うなら、"
                "たぶん良い香りやろな",
                {"user_text": "どうにおいが感じるって"},
            )
        )
        self.assertTrue(
            llm._is_style_acceptable(
                "player_chat",
                "オレにはわかんないけど、プレイヤーは何か良い香りがしたん？",
                {"user_text": "どうにおいが感じるって"},
            )
        )
        self.assertTrue(
            llm._is_style_acceptable(
                "player_chat",
                "何か匂ってたら教えてな。",
                {"user_text": "どうにおいが感じるって"},
            )
        )
        self.assertFalse(
            llm._is_style_acceptable(
                "player_chat",
                "なんか花の匂いがするで。",
                {"user_text": "どうにおいが感じるって"},
            )
        )
        self.assertFalse(
            llm._is_style_acceptable(
                "player_chat",
                "花の匂いがするで、どうする？",
                {"user_text": "どうにおいが感じるって"},
            )
        )

    def test_player_chat_discusses_one_repair_then_accepts_it(self) -> None:
        replies = iter(("例1: 今日はええ天気やな。", "今日はええ天気やな。"))

        class PlayerChatLLM(DogidoLLM):
            def __init__(self, settings):  # type: ignore[no-untyped-def]
                super().__init__(settings)
                self.requests = []
                self.message_sets = []

            def enabled(self) -> bool:
                return True

            def _generate_backend_text(self, request):  # type: ignore[override]
                self.requests.append(request)
                self.message_sets.append(self._build_messages(request))
                return next(replies)

        llm = PlayerChatLLM(
            Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop")
        )
        with self.assertLogs("uvicorn.error", level="WARNING") as captured:
            text = llm.generate_leaf_text(
                LeafGenerationRequest(
                    kind="player_chat",
                    fallback_text="ようわからん、もうちょい教えて。",
                    details={"user_text": "今日はええ天気やな"},
                )
            )

        self.assertEqual(text, "今日はええ天気やな。")
        self.assertEqual(len(llm.requests), 2)
        self.assertTrue(any("result=repair_requested" in row for row in captured.output))
        self.assertTrue(any("result=repair_accepted" in row for row in captured.output))
        self.assertTrue(any("repair_duration_ms=" in row for row in captured.output))
        repair = llm.requests[1].details["player_chat_repair"]
        self.assertEqual(repair["reason"], "meta_role_label")
        self.assertEqual(llm.message_sets[1][-2]["role"], "assistant")
        self.assertEqual(
            llm.message_sets[1][-2]["content"],
            "例1: 今日はええ天気やな。",
        )
        self.assertIn("名前や役割のラベル", llm.message_sets[1][-1]["content"])

    def test_player_chat_can_repair_grounding_but_cannot_bypass_it(self) -> None:
        replies = iter(("なんか花の匂いがするで。", "今日は静かでええな。"))

        class RepairingLLM(DogidoLLM):
            def enabled(self) -> bool:
                return True

            def _generate_backend_text(self, request):  # type: ignore[override]
                return next(replies)

        llm = RepairingLLM(
            Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop")
        )
        request = LeafGenerationRequest(
            kind="player_chat",
            fallback_text="ようわからん、もうちょい教えて。",
            details={"user_text": "今日は静かやな"},
        )
        self.assertEqual(llm.generate_leaf_text(request), "今日は静かでええな。")

        bad_replies = iter(("ゾンビがおる、じっとしてや。", "まだじっとしてや。"))

        class UnsafeLLM(DogidoLLM):
            def __init__(self, settings):  # type: ignore[no-untyped-def]
                super().__init__(settings)
                self.calls = 0

            def enabled(self) -> bool:
                return True

            def _generate_backend_text(self, request):  # type: ignore[override]
                self.calls += 1
                return next(bad_replies)

        unsafe_llm = UnsafeLLM(
            Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop")
        )
        unsafe_request = LeafGenerationRequest(
            kind="player_chat",
            fallback_text="危ない、気いつけてや！",
            details={
                "user_text": "どうしたらええ？",
                "combat_active": True,
                "nearby_hostile_types": ["zombie"],
                "speech_whitelist_enforce": False,
            },
        )
        with self.assertLogs("uvicorn.error", level="WARNING") as captured:
            self.assertEqual(
                unsafe_llm.generate_leaf_text(unsafe_request),
                "危ない、気いつけてや！",
            )
        self.assertEqual(unsafe_llm.calls, 2)
        self.assertTrue(any("result=repair_rejected" in row for row in captured.output))

    def test_player_chat_repair_generation_error_falls_back_without_retrying(self) -> None:
        class FailingRepairLLM(DogidoLLM):
            def __init__(self, settings):  # type: ignore[no-untyped-def]
                super().__init__(settings)
                self.calls = 0

            def enabled(self) -> bool:
                return True

            def _generate_backend_text(self, request):  # type: ignore[override]
                self.calls += 1
                if self.calls == 1:
                    return "例1: 今日は静かやな。"
                raise RuntimeError("repair unavailable")

        llm = FailingRepairLLM(
            Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop")
        )
        with self.assertLogs("uvicorn.error", level="WARNING") as captured:
            text = llm.generate_leaf_text(
                LeafGenerationRequest(
                    kind="player_chat",
                    fallback_text="ようわからん、もうちょい教えて。",
                    details={"user_text": "今日は静かやな"},
                )
            )

        self.assertEqual(text, "ようわからん、もうちょい教えて。")
        self.assertEqual(llm.calls, 2)
        self.assertTrue(any("result=repair_failed" in row for row in captured.output))

    def test_common_short_ascii_in_japanese_reply_is_usable(self) -> None:
        self.assertTrue(
            self.llm._is_usable_output("今の俳句の案、OKってこと？えらいことやん。")
        )
        self.assertFalse(self.llm._is_usable_output("今の案はOKAY desu"))

    def test_player_chat_corrects_observed_name_before_style_whitelist(self) -> None:
        class PlayerChatLLM(DogidoLLM):
            def enabled(self) -> bool:
                return True

            def _generate_backend_text(self, request):  # type: ignore[override]
                return "あれ、ゾンビやったんか。ほんま怖かったわ。"

        llm = PlayerChatLLM(
            Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop")
        )
        text = llm.generate_leaf_text(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="ようわからん、もうちょい教えて。",
                details={
                    "allowed_speech_labels": ["村人ゾンビ"],
                    "speech_whitelist_enforce": True,
                    "speech_name_corrections": {"ゾンビ": "村人ゾンビ"},
                },
            )
        )

        self.assertEqual(text, "あれ、村人ゾンビやったんか。ほんま怖かったわ。")

    def test_openai_compatible_backend_uses_chat_completions(self) -> None:
        llm = DogidoLLM(
            Settings(
                audio_enabled=False,
                llm_enabled=True,
                llm_backend="openai_compatible",
                llm_provider="local",
                llm_base_url="http://127.0.0.1:8080/v1",
                llm_model="mlx-community/Qwen3.6-35B-A3B-4bit-DWQ",
            )
        )
        request = LeafGenerationRequest(
            kind="occluded_entry_no_light",
            fallback_text="fallback",
            details={"player_name": "メルちゃん", "biome": "plains", "time_phase": "night", "craftable": False, "local_light": 7},
            temperature=0.42,
        )

        def handler(req: httpx.Request) -> httpx.Response:
            self.assertEqual(req.url, httpx.URL("http://127.0.0.1:8080/v1/chat/completions"))
            body = json_from_request(req)
            self.assertEqual(body["model"], "mlx-community/Qwen3.6-35B-A3B-4bit-DWQ")
            self.assertEqual(body["messages"][0]["role"], "system")
            self.assertEqual(body["messages"][1]["role"], "user")
            self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "メルちゃん、その暗さでほんまに入るん？ ちょっと怖いわ……。"
                            }
                        }
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        with patch("dogido_server.llm.httpx.Client", return_value=httpx.Client(transport=transport, base_url="http://127.0.0.1:8080/v1/")):
            text = llm.generate_leaf_text(request)

        self.assertEqual(text, "メルちゃん、その暗さでほんまに入るん？ ちょっと怖いわ……。")

    def test_openai_compatible_backend_falls_back_to_reasoning_field(self) -> None:
        llm = DogidoLLM(
            Settings(
                audio_enabled=False,
                llm_enabled=True,
                llm_backend="openai_compatible",
                llm_provider="local",
                llm_base_url="http://127.0.0.1:8080/v1",
                llm_model="mlx-community/Qwen3.6-35B-A3B-4bit-DWQ",
            )
        )
        request = LeafGenerationRequest(
            kind="occluded_entry_no_light",
            fallback_text="fallback",
            details={"player_name": "メルちゃん", "biome": "plains", "time_phase": "night", "craftable": False, "local_light": 7},
            temperature=0.42,
        )

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "reasoning": "Final answer: メルちゃん、その暗さでほんまに入るん？ ちょっと怖いわ……。"
                            }
                        }
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        with patch("dogido_server.llm.httpx.Client", return_value=httpx.Client(transport=transport, base_url="http://127.0.0.1:8080/v1/")):
            text = llm.generate_leaf_text(request)

        self.assertEqual(text, "メルちゃん、その暗さでほんまに入るん？ ちょっと怖いわ……。")

    def test_chat_completions_openai_provider_uses_default_base_url(self) -> None:
        llm = DogidoLLM(
            Settings(
                audio_enabled=False,
                llm_enabled=True,
                llm_backend="chat_completions",
                llm_provider="openai",
                llm_model="gpt-4.1-mini",
                llm_api_key="sk-test",
            )
        )
        request = LeafGenerationRequest(
            kind="occluded_entry_no_light",
            fallback_text="fallback",
            details={"player_name": "メルちゃん", "biome": "plains", "time_phase": "night", "craftable": False, "local_light": 7},
            temperature=0.42,
        )

        def handler(req: httpx.Request) -> httpx.Response:
            self.assertEqual(req.url, httpx.URL("https://api.openai.com/v1/chat/completions"))
            self.assertEqual(req.headers.get("Authorization"), "Bearer sk-test")
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "メルちゃん、その暗さでほんまに入るん？ ちょっと怖いわ……。"
                            }
                        }
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client

        def build_client(*args, **kwargs):
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)

        with patch("dogido_server.llm.httpx.Client", side_effect=build_client):
            text = llm.generate_leaf_text(request)

        self.assertEqual(text, "メルちゃん、その暗さでほんまに入るん？ ちょっと怖いわ……。")

    def test_generate_structured_json_parses_fenced_object(self) -> None:
        class StructuredLLM(DogidoLLM):
            def enabled(self) -> bool:
                return True

            def _generate_backend_text(self, request):
                return '```json\n{"found": true, "kind": "contrast", "description": "砂漠なのに熱帯魚", "elements": ["砂漠", "熱帯魚"]}\n```'

        llm = StructuredLLM(Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop"))
        payload = llm.generate_structured_json(
            StructuredGenerationRequest(
                kind="haiku_irony",
                fallback_value={"found": False},
                details={"feature_candidates": ["バイオーム 砂漠"]},
            )
        )

        self.assertTrue(payload["found"])
        self.assertEqual(payload["kind"], "contrast")
        self.assertEqual(payload["elements"], ["砂漠", "熱帯魚"])

    def test_mlx_model_cache_is_shared_across_clients(self) -> None:
        DogidoLLM._shared_mlx_models.clear()
        first = DogidoLLM(
            Settings(
                audio_enabled=False,
                llm_enabled=True,
                llm_backend="mlx",
                mlx_model_id="mlx-community/Qwen3.6-35B-A3B-4bit-DWQ",
            )
        )
        second = DogidoLLM(
            Settings(
                audio_enabled=False,
                llm_enabled=True,
                llm_backend="mlx",
                mlx_model_id="mlx-community/Qwen3.6-35B-A3B-4bit-DWQ",
                llm_max_tokens=192,
            )
        )
        load_calls: list[str] = []

        class FakeMlx:
            def load(self, model_id: str):
                load_calls.append(model_id)
                return ("MODEL", "TOKENIZER")

        with patch("dogido_server.llm.client.importlib.import_module", return_value=FakeMlx()):
            self.assertTrue(first.preload())
            self.assertTrue(second.preload())

        self.assertEqual(load_calls, ["mlx-community/Qwen3.6-35B-A3B-4bit-DWQ"])

    def test_generate_structured_json_logs_raw_invalid_haiku_irony_output(self) -> None:
        class StructuredLLM(DogidoLLM):
            def enabled(self) -> bool:
                return True

            def _generate_backend_text(self, request):
                return "青いじゃがいもが踊る"

        llm = StructuredLLM(Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop"))
        with self.assertLogs("uvicorn.error", level="WARNING") as captured:
            payload = llm.generate_structured_json(
                StructuredGenerationRequest(
                    kind="haiku_irony",
                    fallback_value={"found": False},
                    details={"feature_candidates": ["バイオーム 森"]},
                )
            )

        self.assertEqual(payload, {"found": False, "__dogido_status": "invalid_json"})
        self.assertTrue(
            any(
                "llm_structured kind=haiku_irony result=fallback reason=invalid_json raw=青いじゃがいもが踊る"
                in line
                for line in captured.output
            )
        )

    def test_haiku_grounding_leaves_single_result_for_domain_consumer(self) -> None:
        class StructuredLLM(DogidoLLM):
            def __init__(self) -> None:
                super().__init__(
                    Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop")
                )
                self.calls = 0

            def enabled(self) -> bool:
                return True

            def _generate_backend_text(self, request):
                self.calls += 1
                if self.calls == 1:
                    return '{"line_index":0,"atom_ids":["observation:test:0"],"meaning_retained":true,"natural_japanese":true,"reason":"一致"}'
                return '{"assessments":[{"line_index":0,"atom_ids":["observation:test:0"],"meaning_retained":true,"natural_japanese":true,"reason":"一致"}]}'

        llm = StructuredLLM()
        payload = llm.generate_structured_json(
            StructuredGenerationRequest(
                kind="haiku_line_grounding",
                fallback_value={"assessments": []},
                details={
                    "grounding_lines": [{"line_index": 0, "text": "はるのかぜ"}],
                    "source_atoms": [
                        {"atom_id": "observation:test:0", "text": "春の風"}
                    ],
                },
            )
        )

        self.assertEqual(1, llm.calls)
        self.assertEqual("accepted", payload["__dogido_status"])
        self.assertEqual(0, payload["line_index"])

    def test_haiku_scene_leaves_single_clause_for_domain_consumer(self) -> None:
        class StructuredLLM(DogidoLLM):
            def __init__(self) -> None:
                super().__init__(
                    Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop")
                )
                self.calls = 0

            def enabled(self) -> bool:
                return True

            def _generate_backend_text(self, request):
                self.calls += 1
                if self.calls == 1:
                    return '{"text":"夕暮れの草地と白い骨が溶け合っとるな","basis_atom_ids":["observation:test:0"],"claim_class":"interpretive"}'
                return '{"found":true,"clauses":[{"text":"夕暮れの草地と白い骨が溶け合っとるな","basis_atom_ids":["observation:test:0"],"claim_class":"interpretive"}],"motifs":["夕暮れ","骨"],"focus":["光と色"],"confidence":0.9}'

        llm = StructuredLLM()
        payload = llm.generate_structured_json(
            StructuredGenerationRequest(
                kind="haiku_scene",
                fallback_value={"found": False},
                details={
                    "source_atoms": [
                        {
                            "atom_id": "observation:test:0",
                            "text": "夕暮れの草地で白い骨を持っている",
                        }
                    ],
                },
            )
        )

        self.assertEqual(1, llm.calls)
        self.assertEqual("accepted", payload["__dogido_status"])
        self.assertEqual(
            "夕暮れの草地と白い骨が溶け合っとるな",
            payload["text"],
        )

    def test_generate_structured_json_uses_request_max_tokens_override(self) -> None:
        llm = DogidoLLM(
            Settings(
                audio_enabled=False,
                llm_enabled=True,
                llm_backend="openai_compatible",
                llm_provider="local",
                llm_base_url="http://127.0.0.1:8080/v1",
                llm_model="mlx-community/Qwen3.6-35B-A3B-4bit-DWQ",
                llm_max_tokens=72,
            )
        )
        request = StructuredGenerationRequest(
            kind="haiku_scene",
            fallback_value={"found": False},
            details={
                "feature_candidates": ["バイオーム 草地"],
                "source_atoms": [
                    {"atom_id": "observation:test:0", "text": "朝の草原"}
                ],
            },
            max_tokens=192,
        )

        def handler(req: httpx.Request) -> httpx.Response:
            body = json_from_request(req)
            self.assertEqual(body["max_tokens"], 192)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"found": true, "clauses": [{"text": "朝の草原で手持ちの焚き火台", "basis_atom_ids": ["observation:test:0"], "claim_class": "interpretive"}], "motifs": ["朝", "草原"], "focus": ["草原"], "confidence": 0.8}'
                            }
                        }
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        with patch("dogido_server.llm.httpx.Client", return_value=httpx.Client(transport=transport, base_url="http://127.0.0.1:8080/v1/")):
            payload = llm.generate_structured_json(request)

        self.assertTrue(payload["found"])
        self.assertEqual(payload["clauses"][0]["text"], "朝の草原で手持ちの焚き火台")

    def test_route_settings_auto_resolve_remote_backends(self) -> None:
        settings = Settings(
            audio_enabled=False,
            llm_enabled=True,
            llm_backend="mlx",
            llm_provider="local",
            mlx_model_id="mlx-community/Qwen3.6-35B-A3B-4bit-DWQ",
            llm_chat_provider="openai",
            llm_chat_model="gpt-4.1-mini",
            llm_haiku_provider="claude",
            llm_haiku_model="claude-sonnet-4-20250514",
            llm_haiku_api_key="test-key",
        )

        chat_settings = settings.llm_route_settings("chat")
        haiku_settings = settings.llm_route_settings("haiku")

        self.assertEqual(chat_settings.llm_effective_backend, "chat_completions")
        self.assertEqual(chat_settings.llm_provider, "openai")
        self.assertEqual(haiku_settings.llm_effective_backend, "anthropic_messages")
        self.assertEqual(haiku_settings.llm_provider, "claude")

    def test_dark_push_after_breath_rejects_stylistic_drift(self) -> None:
        llm = DogidoLLM(Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop"))

        self.assertFalse(llm._is_style_acceptable("dark_push_after_breath", "メルちゃん、ここの闇、まるで心臓が凍りつくみたいだ…！"))
        self.assertFalse(llm._is_style_acceptable("dark_push_no_light", "メルちゃん、暗闇が広がってるんだが、光が全くないんだよね。"))
        self.assertFalse(llm._is_style_acceptable("occluded_entry_no_light", "そんなとこ、行くんか？ 暗すぎてやんか…？ やんか？ やんか？ やんか？"))
        self.assertFalse(llm._is_style_acceptable("occluded_entry_no_light", "メルちゃん、そんな暗い洞窟、行くんかやんか？やわ。"))
        self.assertFalse(llm._is_style_acceptable("newly_burning_visual", "やばい、めっちゃ燃えてる！助かったわ〜"))
        self.assertFalse(llm._is_style_acceptable("newly_burning_visual", "あ、燃えてる！助かったぁ、ほんとに助かったね"))
        self.assertFalse(llm._is_style_acceptable("daylight_water_skeleton", "メルちゃん、スケルトンが水で消えとる！火つけてや～！"))
        self.assertFalse(llm._is_style_acceptable("aftermath", "メルちゃん、クリーパーの爆発音が耳に残るわ。"))
        self.assertFalse(llm._is_style_acceptable("aftermath", "プレイヤー、体力9.45やから、次は絶対逃げようね。"))
        self.assertFalse(llm._is_style_acceptable("aftermath", "メルちゃん、HP7やし回復しようや。"))
        self.assertFalse(llm._is_style_acceptable("darkness_escape", "メルちゃん、闇夜で武器もないなんて……怖くて動けへんわ。"))
        self.assertFalse(llm._is_style_acceptable("darkness_escape", "メルちゃん、早く明るい場所に戻ってほしくて仕方ないよ。"))
        self.assertFalse(llm._is_style_acceptable("darkness_escape", "メルちゃん、もっと明るい所へ逃げようよ。"))
        self.assertFalse(llm._is_style_acceptable("darkness_escape", "無理しなくていいから、落ち着いて。"))
        self.assertTrue(llm._is_style_acceptable("dark_push_after_breath", "メルちゃん、心臓に悪いわ……ほんま勘弁してや……。"))
        self.assertTrue(llm._is_style_acceptable("newly_burning_visual", "うわっ、燃えたん助かるわ！そのまま頼むで！"))
        self.assertTrue(llm._is_style_acceptable("daylight_water_skeleton", "うわっ、スケルトン水やん！頼むから岸へ寄って燃えてくれや！"))

    def test_preload_loads_mlx_model_once(self) -> None:
        llm = DogidoLLM(
            Settings(
                audio_enabled=False,
                llm_enabled=True,
                llm_backend="mlx",
                mlx_model_id="mlx-community/test-model",
            )
        )

        class FakeMLX:
            def __init__(self) -> None:
                self.calls = 0

            def load(self, model_id: str):
                self.calls += 1
                self.last_model_id = model_id
                return object(), object()

        fake_mlx = FakeMLX()
        with patch("dogido_server.llm.importlib.import_module", return_value=fake_mlx):
            self.assertTrue(llm.preload())
            self.assertTrue(llm.preload())

        self.assertEqual(fake_mlx.calls, 1)


def json_from_request(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
