# app.py
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from dogido_server.config import Settings, get_settings
from dogido_server.models import (
    AcceptedEventResponse,
    AdapterSessionCreateRequest,
    AdapterSessionCreateResponse,
    BatchAcceptedResponse,
    BatchEventRequest,
    CloseSessionResponse,
    GameEvent,
    HealthResponse,
    HeartbeatRequest,
    HeartbeatResponse,
)
from dogido_server.service import DogidoService


def create_app(settings: Settings | None = None) -> FastAPI:
    # settings を外から注入できるようにしている（テスト時にモック設定を渡すため）
    resolved_settings = settings or get_settings()
    service = DogidoService(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # アプリ起動時に LLM の preload とフォールバック音声の prewarm を走らせる
        service.warmup()
        yield
        # yield 後はシャットダウン処理を書く場所（現時点では何もしない）

    app = FastAPI(
        title=resolved_settings.service_name,
        version=resolved_settings.service_version,
        lifespan=lifespan,
    )
    # app.state にサービスを格納しておくと、テストや将来のミドルウェアから参照できる
    app.state.settings = resolved_settings
    app.state.service = service

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(_, exc: RequestValidationError) -> JSONResponse:
        # Pydantic のバリデーションエラーをそのまま返す
        # body も一緒に返すことでデバッグ時にアダプタ側が原因特定しやすくなる
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": exc.errors(),
                "body": exc.body,
            },
        )

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        # Kubernetes / Docker ヘルスチェック用エンドポイント
        return HealthResponse(
            ok=True,
            service=resolved_settings.service_name,
            version=resolved_settings.service_version,
        )

    @app.post("/api/v1/adapter-sessions", response_model=AdapterSessionCreateResponse, status_code=201)
    async def create_adapter_session(
        payload: AdapterSessionCreateRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> AdapterSessionCreateResponse:
        # Fabric アダプタ起動時にセッションを登録する
        # セッション ID はこの後の game-events / heartbeat に必要
        _ensure_authorized(resolved_settings, authorization)
        return service.create_session(payload)

    @app.post("/api/v1/game-events", response_model=AcceptedEventResponse)
    async def post_game_event(
        payload: GameEvent,
        authorization: Annotated[str | None, Header()] = None,
        x_dogido_session_id: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Response:
        _ensure_authorized(resolved_settings, authorization)
        result = service.process_event(
            payload,
            session_id=x_dogido_session_id,
            idempotency_key=idempotency_key,
        )
        # アクションがあればその場で dispatch する（TTS・M5Stack送信など）
        if result.actions:
            service.dispatch_actions(result.actions)

        # 重複扱いのイベントは 200、新規受付は 202 を返す
        status_code = status.HTTP_200_OK if result.response.deduplicated else status.HTTP_202_ACCEPTED
        return Response(
            content=result.response.model_dump_json(),
            status_code=status_code,
            media_type="application/json",
        )

    @app.post("/api/v1/game-events/batch", response_model=BatchAcceptedResponse, status_code=202)
    async def post_game_event_batch(
        payload: BatchEventRequest,
        authorization: Annotated[str | None, Header()] = None,
        x_dogido_session_id: Annotated[str | None, Header()] = None,
    ) -> BatchAcceptedResponse:
        # バッチ送信は WebSocket 移行前の暫定手段として設けている
        _ensure_authorized(resolved_settings, authorization)
        # 過大なバッチを拒否して処理詰まりを防ぐ
        if len(payload.events) > resolved_settings.max_batch_size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"events exceeds max_batch_size={resolved_settings.max_batch_size}",
            )
        result, actions = service.process_batch(payload.events, session_id=x_dogido_session_id)
        if actions:
            service.dispatch_actions(actions)
        return result

    @app.post("/api/v1/adapter-sessions/{session_id}/heartbeat", response_model=HeartbeatResponse)
    async def post_heartbeat(
        session_id: str,
        payload: HeartbeatRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> HeartbeatResponse:
        # アダプタが生きているかの死活確認と、最後に受け取ったシーケンス番号の記録
        _ensure_authorized(resolved_settings, authorization)
        if session_id not in service.sessions:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown session_id")
        return service.heartbeat(session_id, payload.last_sequence)

    @app.delete("/api/v1/adapter-sessions/{session_id}", response_model=CloseSessionResponse)
    async def delete_session(
        session_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> CloseSessionResponse:
        # アダプタ正常終了時にセッションを明示クローズする
        # 異常終了時はハートビートのタイムアウトで検知する想定
        _ensure_authorized(resolved_settings, authorization)
        return service.close_session(session_id)

    @app.get("/api/v1/memory/haiku")
    async def get_haiku_memory(
        authorization: Annotated[str | None, Header()] = None,
    ) -> list[dict[str, object]]:
        _ensure_authorized(resolved_settings, authorization)
        return service.list_haiku_memory()

    @app.get("/api/v1/memory/profile")
    async def get_memory_profile(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _ensure_authorized(resolved_settings, authorization)
        return service.memory_profile()

    @app.get("/api/v1/memory/summary")
    async def get_memory_summary(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _ensure_authorized(resolved_settings, authorization)
        return service.memory_startup_summary()

    return app


def _ensure_authorized(settings: Settings, authorization: str | None) -> None:
    # auth_token が未設定なら認証スキップ（ローカル開発環境向け）
    if not settings.auth_token:
        return
    expected = f"Bearer {settings.auth_token}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "dogido_server.app:create_app",
        # create_app を factory として呼ぶ（リロード時に再生成される）
        factory=True,
        host=settings.bind_host,
        port=settings.bind_port,
        # 本番運用では reload=False 固定
        reload=False,
    )


# `uvicorn dogido_server.app:app` で直接起動するときのモジュールレベルインスタンス
# テスト・開発時は create_app() を呼んで設定を注入するほうが推奨
app = create_app()
