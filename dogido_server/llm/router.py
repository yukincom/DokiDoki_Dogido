# llm/router.py
from __future__ import annotations

from dogido_server.config import Settings

from .client import DogidoLLM
from .types import LLMFrontend, LeafGenerationRequest, StructuredGenerationRequest


class DogidoLLMRouter:
    """雑談系と川柳系の route を切り替える軽量フロントエンド。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._clients: dict[str, DogidoLLM] = {}
        self._client_by_route: dict[str, DogidoLLM] = {
            "chat": self._client_for_route("chat"),
            "haiku": self._client_for_route("haiku"),
        }

    def preload(self) -> bool:
        results: list[bool] = []
        seen: set[int] = set()
        for client in self._client_by_route.values():
            identity = id(client)
            if identity in seen:
                continue
            seen.add(identity)
            results.append(client.preload())
        return any(results)

    def generate_leaf_text(self, request: LeafGenerationRequest) -> str:
        return self._resolve_client(request.route, request.kind).generate_leaf_text(request)

    def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
        return self._resolve_client(request.route, request.kind).generate_structured_json(request)

    def _client_for_route(self, route: str) -> DogidoLLM:
        signature = self.settings.llm_route_signature(route)  # type: ignore[arg-type]
        key = repr(signature)
        existing = self._clients.get(key)
        if existing is not None:
            return existing
        client = DogidoLLM(self.settings.llm_route_settings(route))  # type: ignore[arg-type]
        self._clients[key] = client
        return client

    def _resolve_client(self, route: str | None, kind: str) -> LLMFrontend:
        resolved_route = route or self._default_route_for_kind(kind)
        return self._client_by_route.get(resolved_route, self._client_by_route["chat"])

    def _default_route_for_kind(self, kind: str) -> str:
        if kind == "haiku":
            return "haiku"
        return "chat"
