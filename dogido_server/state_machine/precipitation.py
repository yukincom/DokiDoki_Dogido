"""標高・バイオーム気候・実観測から、現在地の降水と雪の根拠を決める。

LLM に気温や降雪開始高度を解釈させない。コードで現在 Y と比較し、
「降っている雪」と「地面で実測した積雪」を分けて川柳・雑談へ共有する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal


SNOW_SURFACE_BLOCKS = frozenset({"snow", "snow_block", "powder_snow"})
SnowEvidence = Literal["observed_surface", "active_snowfall", "none"]
PrecipitationKind = Literal["snow", "rain", "none", "unknown"]


@dataclass(frozen=True, slots=True)
class PrecipitationContext:
    current_y: int | None
    biome_temperature: float | None
    snow_start_y: int | None
    snowfall_zone: bool | None
    precipitation_kind: PrecipitationKind
    surface_snow_observed: bool
    snow_evidence: SnowEvidence

    @property
    def snow_can_be_scene_material(self) -> bool:
        """降っている雪か実測した地表雪だけを、現在場面の材料にする。"""

        return self.snow_evidence != "none"

    def prompt_line(self) -> str:
        """数値と採用境界を一つの事実行にし、LLM の再解釈を防ぐ。"""

        parts: list[str] = []
        if self.current_y is not None:
            parts.append(f"現在Y{self.current_y}")
        if self.biome_temperature is not None:
            parts.append(f"バイオーム基準気温{self.biome_temperature:g}")
        if self.snow_start_y is not None:
            if self.snowfall_zone is True:
                parts.append(f"現在地は降雪開始Y{self.snow_start_y}以上")
            elif self.snowfall_zone is False:
                parts.append(f"現在地は降雪開始Y{self.snow_start_y}未満")
            else:
                parts.append(f"降雪開始Y{self.snow_start_y}との比較は判定不能")

        if self.precipitation_kind == "snow":
            parts.append("現在の降水は雪")
        elif self.precipitation_kind == "rain":
            parts.append("現在の降水は雨")
        elif self.precipitation_kind == "none":
            parts.append("現在は降水なし")
        else:
            parts.append("現在の降水種別は不明")

        if self.surface_snow_observed:
            parts.append("周辺に雪・雪ブロックを実測")
            parts.append("地表の雪を現在場面の材料にできる")
        elif self.precipitation_kind == "snow":
            parts.append("地表の積雪は未観測")
            parts.append("降っている雪だけを現在場面の材料にできる")
        else:
            parts.append("周辺の積雪は未観測")
            parts.append("雪や積雪を現在場面の材料にしない")
        return "。".join(parts) + "。"

    def to_prompt_details(self) -> dict[str, object]:
        return {
            "current_y": self.current_y,
            "biome_temperature": self.biome_temperature,
            "snow_start_y": self.snow_start_y,
            "snowfall_zone": self.snowfall_zone,
            "precipitation_kind": self.precipitation_kind,
            "surface_snow_observed": self.surface_snow_observed,
            "snow_evidence": self.snow_evidence,
            "snow_context": self.prompt_line(),
        }


def resolve_precipitation_context(
    *,
    current_y: float | None,
    biome_temperature: float | None,
    snow_start_y: int | None,
    biome_group_id: str,
    biome_downfall: float | None,
    weather: str,
    dimension: str | None,
    nearby_block_names: Iterable[str],
) -> PrecipitationContext:
    """Minecraft の観測値を、会話で使える閉じた降水状態へ変換する。"""

    rounded_y = int(round(current_y)) if current_y is not None else None
    normalized_dimension = str(dimension or "").removeprefix("minecraft:").lower()
    normalized_weather = str(weather or "").lower()
    normalized_group = str(biome_group_id or "").lower()
    surface_snow_observed = any(
        str(name or "").removeprefix("minecraft:").lower() in SNOW_SURFACE_BLOCKS
        for name in nearby_block_names
    )

    precipitation_disabled = (
        normalized_dimension in {"the_nether", "the_end"}
        or normalized_group == "dry"
        or (biome_downfall is not None and biome_downfall <= 0.0)
    )
    if precipitation_disabled:
        snowfall_zone: bool | None = False
    elif snow_start_y is not None:
        snowfall_zone = rounded_y >= snow_start_y if rounded_y is not None else None
    elif biome_temperature is not None:
        # Minecraft の降雪境界。高度別の例外は catalog の snow_start_y を優先する。
        snowfall_zone = biome_temperature < 0.15
    else:
        snowfall_zone = None

    if normalized_weather == "clear" or precipitation_disabled:
        precipitation_kind: PrecipitationKind = "none"
    elif normalized_weather in {"rain", "thunder"}:
        if snowfall_zone is True:
            precipitation_kind = "snow"
        elif snowfall_zone is False:
            precipitation_kind = "rain"
        else:
            precipitation_kind = "unknown"
    else:
        precipitation_kind = "unknown"

    snow_evidence: SnowEvidence = (
        "observed_surface"
        if surface_snow_observed
        else "active_snowfall"
        if precipitation_kind == "snow"
        else "none"
    )
    return PrecipitationContext(
        current_y=rounded_y,
        biome_temperature=biome_temperature,
        snow_start_y=snow_start_y,
        snowfall_zone=snowfall_zone,
        precipitation_kind=precipitation_kind,
        surface_snow_observed=surface_snow_observed,
        snow_evidence=snow_evidence,
    )
