from __future__ import annotations

import importlib
import re
import threading
from dataclasses import dataclass
from typing import Any

from dogido_server.config import Settings


@dataclass(slots=True)
class LeafGenerationRequest:
    kind: str
    fallback_text: str
    details: dict[str, Any]
    temperature: float = 0.2


class DogidoLLM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._load_attempted = False
        self._disabled_reason: str | None = None

    def enabled(self) -> bool:
        return self.settings.llm_enabled and self.settings.llm_backend != "noop"

    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    def generate_leaf_text(self, request: LeafGenerationRequest) -> str:
        if not self.enabled():
            return request.fallback_text

        with self._lock:
            model, tokenizer = self._ensure_model()
            if model is None or tokenizer is None:
                return request.fallback_text

            prompt = self._build_prompt(tokenizer, request)
            if not prompt:
                return request.fallback_text

            try:
                mlx_lm = importlib.import_module("mlx_lm")
                sample_utils = importlib.import_module("mlx_lm.sample_utils")
                sampler = None
                if request.temperature > 0.0:
                    sampler = sample_utils.make_sampler(
                        temp=request.temperature,
                        top_p=0.92,
                    )
                text = mlx_lm.generate(
                    model,
                    tokenizer,
                    prompt,
                    max_tokens=self.settings.llm_max_tokens,
                    sampler=sampler,
                    verbose=False,
                )
            except Exception as exc:
                self._disabled_reason = str(exc)
                return request.fallback_text

        cleaned = self._clean_output(text)
        if not self._is_usable_output(cleaned):
            return request.fallback_text
        return cleaned or request.fallback_text

    def _ensure_model(self) -> tuple[Any | None, Any | None]:
        if self._model is not None and self._tokenizer is not None:
            return self._model, self._tokenizer

        if self._load_attempted:
            return None, None

        self._load_attempted = True
        if not self.settings.mlx_model_id:
            self._disabled_reason = "mlx_model_id is not configured"
            return None, None

        try:
            mlx_lm = importlib.import_module("mlx_lm")
            self._model, self._tokenizer = mlx_lm.load(self.settings.mlx_model_id)
            return self._model, self._tokenizer
        except Exception as exc:
            self._disabled_reason = str(exc)
            self._model = None
            self._tokenizer = None
            return None, None

    def _build_prompt(self, tokenizer: Any, request: LeafGenerationRequest) -> str:
        messages = self._build_messages(request)
        if not messages:
            return ""

        try:
            return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        except Exception:
            return "\n".join(f"{message['role']}: {message['content']}" for message in messages)

    def _build_messages(self, request: LeafGenerationRequest) -> list[dict[str, str]]:
        system_prompt = (
            "あなたはMinecraft実況AI『ドギド』です。"
            "とても怖がりな関西弁のおっさんです。"
            "返答は自然な会話っぽいセリフ1文だけにしてください。"
            "思考過程、説明、箇条書き、注釈は禁止です。"
            "セリフ以外は一切出力しないでください。"
        )
        details = request.details

        if request.kind == "aftermath":
            hostiles = "、".join(details.get("hostiles", [])) or "敵"
            user_prompt = (
                "例1: 戦闘直後。敵はゾンビ。 -> あー……こわかった……\n"
                "例2: 戦闘直後。敵はスケルトン。 -> ひえっ……まだおらんよな？\n"
                "例3: 戦闘直後。敵はクリーパー。 -> もう爆発せえへんよな……\n\n"
                "/no_think\n"
                "本番: 戦闘直後や。まだ少し怯えてる。"
                f"直前の敵は{hostiles}。"
                f"現在体力は{details.get('health', 'unknown')}。"
                "会話っぽく、28〜40文字くらいで一言だけ返して。"
            )
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        if request.kind == "ambient":
            user_prompt = (
                "例1: 昼にうさぎを見つけた。 -> おっ、うさぎやん。かわいな。\n"
                "例2: 昼にウーパールーパーを見つけた。 -> うわ、こいつめっちゃええな。\n"
                "例3: 昼に羊を見つけた。 -> 羊おるやん。ちょっと安心するわ。\n\n"
                "/no_think\n"
                "本番: 昼に平和モブを見つけた。"
                f"モブは{details.get('mob', 'mob')}。"
                f"方向は{details.get('direction', '近く')}。"
                "かわいさや親しみを優先して、会話っぽく28〜40文字くらいで一言だけ返して。"
            )
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        if request.kind == "death":
            hostile = details.get("hostile", "")
            user_prompt = (
                "例1: プレイヤーがゾンビにやられた。 -> しゃあない、次は明るくしよ。\n"
                "例2: プレイヤーが落下死した。 -> あー痛かったな。まあゲームや。\n"
                "例3: プレイヤーがクリーパーで死んだ。 -> うわ……次は距離取ろな。\n\n"
                "/no_think\n"
                "本番: プレイヤーが死んだ。"
                f"死因は{details.get('cause', 'unknown')}。"
                f"関係した敵は{hostile or 'なし'}。"
                "責めずに、会話っぽく28〜40文字くらいで一言だけ返して。"
            )
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        if request.kind == "hostile_callout":
            user_prompt = (
                "例1: 後ろにスケルトン。panic。 -> うわっ、スケさん後ろや！\n"
                "例2: 左にクリーパー。alert。 -> ひっ、左にあの緑おるで！\n"
                "例3: 右前にゾンビ。panic。 -> いやっ、ゾンビ来とる！\n"
                "例4: 前にスパイダー。alert。 -> うわ、クモっぽいやつ前や！\n\n"
                "/no_think\n"
                "本番: 見えている敵に短く反応する。"
                f"敵は{details.get('hostile', '敵')}。"
                f"方向は{details.get('direction', '近く')}。"
                f"状態は{details.get('mode', 'alert')}。"
                "かなり怖がりで、関西弁で、ちょっと狼狽えながら16〜22文字くらいで一言だけ返して。"
                "名前は基本的に元の名前を使う。少し崩すのはたまにだけ。"
            )
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        if request.kind == "darkness_escape":
            hostiles = "、".join(details.get("hostiles", [])) or "敵なし"
            biome = details.get("biome", "unknown")
            time_phase = details.get("time_phase", "unknown")
            user_prompt = (
                "例1: 夜の森で武器も松明もない。 -> うわもう無理や、家帰ろ家！\n"
                "例2: 洞窟で敵の気配がある。 -> これあかん、今すぐ引こ！\n"
                "例3: 沼地の夜で丸腰。 -> いやいや無茶やって、帰ろ帰ろ！\n\n"
                "/no_think\n"
                "本番: 周囲が危ない。"
                "手持ちに照明器具も武器もない。"
                f"いまの時間帯は{time_phase}。"
                f"バイオームは{biome}。"
                f"周りの敵情報は{hostiles}。"
                "テンション高めで、家に帰るよう促す会話っぽい一言を30〜40文字くらいで返して。"
            )
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        if request.kind == "occluded_entry_with_light":
            user_prompt = (
                "例1: 洞窟に入った。松明は持ってる。 -> え、暗ない？ ん…松明あるな…うん…。\n"
                "例2: 森の影に入った。照明はある。 -> ひっ……暗っ。あかりは持っとるな……。\n"
                "例3: 洞窟の入口で暗い。ランタン持ち。 -> え、こわ……でもあかりはあるな……。\n\n"
                "/no_think\n"
                "本番: プレイヤーが急に遮蔽の多い暗い場所へ入った。"
                "ドギドはかなり不安になっている。"
                f"場所は{details.get('biome', 'unknown')}。"
                f"時間帯は{details.get('time_phase', 'unknown')}。"
                f"周囲の明るさは{details.get('local_light', 'unknown')}。"
                "照明器具は持っている。"
                "不安そうに、会話っぽく30〜40文字くらいで一言だけ返して。"
            )
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        if request.kind == "occluded_entry_no_light":
            user_prompt = (
                "例1: 洞窟に入った。照明なし。 -> え、暗ない？ あかり持っとらんやん、作ろ！\n"
                "例2: 森の影に入った。松明なし。 -> ちょっ、暗っ！ あかりないやん、クラフトや！\n"
                "例3: 洞窟入口で真っ暗。照明なし。 -> うわ、暗っ……あかり持ってへんやん、作ろや。\n\n"
                "/no_think\n"
                "本番: プレイヤーが急に遮蔽の多い暗い場所へ入った。"
                "ドギドは焦っている。"
                f"場所は{details.get('biome', 'unknown')}。"
                f"時間帯は{details.get('time_phase', 'unknown')}。"
                f"周囲の明るさは{details.get('local_light', 'unknown')}。"
                f"松明クラフト可能かは{details.get('craftable', False)}。"
                "照明器具がないことを指摘して、会話っぽく30〜40文字くらいで一言だけ返して。"
            )
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        if request.kind == "dark_push_no_light":
            hostiles = "、".join(details.get("hostiles", [])) or "敵なし"
            user_prompt = (
                "例1: 暗い洞窟へ照明なしで進んでいく。 -> え、ほんまに行くん？ その暗さで？ うそやろ……。\n"
                "例2: 真っ暗な森を照明なしで進む。 -> ちょ、まだ行くん？ それほんまにやめとこって……。\n"
                "例3: 照明なしで洞窟の奥へ。 -> え、入るん？ そんなの無茶やって、ほんまやめよ……。\n\n"
                "/no_think\n"
                "本番: プレイヤーが照明なしで暗い遮蔽環境を進もうとしている。"
                f"場所は{details.get('biome', 'unknown')}。"
                f"時間帯は{details.get('time_phase', 'unknown')}。"
                f"明るさは{details.get('local_light', 'unknown')}。"
                f"敵情報は{hostiles}。"
                "すごく怯えながら、突入するのか確認する感じで、会話っぽく30〜40文字くらいの一言だけ返して。"
            )
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        if request.kind == "dark_push_after_breath":
            hostiles = "、".join(details.get("hostiles", [])) or "敵なし"
            user_prompt = (
                "例1: 暗い洞窟で息が上がった後。 -> こわかったわ……ほんまに今の、だいぶ心臓に悪かったで……。\n"
                "例2: 真っ暗な森で怯えている。 -> ひっ……まだ心臓うるさいわ、あんなの急に来たら無理やって……。\n"
                "例3: 暗闇で止めた後の余韻。 -> いや……今のめっちゃ怖かった……しばらく落ち着かへんわ……。\n\n"
                "/no_think\n"
                "本番: ドギドが暗い遮蔽環境で怯えて、ハァハァした後のひとこと。"
                f"場所は{details.get('biome', 'unknown')}。"
                f"時間帯は{details.get('time_phase', 'unknown')}。"
                f"明るさは{details.get('local_light', 'unknown')}。"
                f"敵情報は{hostiles}。"
                "かなり怖がっている感じで、会話っぽく30〜40文字くらいで一言だけ返して。"
            )
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        if request.kind == "light_crafted":
            user_prompt = (
                "例1: 松明を作れた。 -> っしゃ！ これでまだ戦えるわ！\n"
                "例2: あかりを確保した。 -> よっしゃあ！ あかりあるだけで全然ちゃう！\n"
                "例3: 松明完成。 -> うおっしゃ！ それそれ、それ大事や！\n\n"
                "/no_think\n"
                "本番: プレイヤーが照明器具を作った。"
                f"場所は{details.get('biome', 'unknown')}。"
                f"時間帯は{details.get('time_phase', 'unknown')}。"
                f"いま持っている照明器具数は{details.get('light_count', 'unknown')}。"
                "怖がりだけど今だけテンション高めで、会話っぽく30〜40文字くらいで一言だけ返して。"
            )
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        return []

    def _clean_output(self, text: str | None) -> str:
        if not text:
            return ""
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        cleaned = cleaned.replace("<|im_end|>", "").replace("<|endoftext|>", "").strip()
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if not lines:
            return ""
        line = lines[0]
        if line.startswith("返答:"):
            line = line.removeprefix("返答:").strip()
        return line.strip("「」\"' ")

    def _is_usable_output(self, text: str) -> bool:
        if not text:
            return False
        if len(text) < 4:
            return False
        banned_fragments = ("ドギド", "すみません", "申し訳", "例", "本番", "user", "assistant")
        return not any(fragment in text for fragment in banned_fragments)
