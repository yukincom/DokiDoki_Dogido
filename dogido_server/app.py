# app.py
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial
from typing import Annotated, Any, Callable, TypeVar

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
    PlayerInputRequest,
    TrainingFeedbackRequest,
    TrainingFeedbackResponse,
    VoiceInputContextResponse,
)
from dogido_server.service import DogidoService


_T = TypeVar("_T")


def create_app(settings: Settings | None = None) -> FastAPI:
    # settings を外から注入できるようにしている（テスト時にモック設定を渡すため）
    resolved_settings = settings or get_settings()
    service = DogidoService(resolved_settings)
    # service はセッション状態を持つため、専用の単一workerへ全操作を積む。
    # handlerが接続断で何度cancelされても、実行中threadと次操作は並行しない。
    service_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dogido-service")

    async def run_serialized(
        function: Callable[..., _T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(service_executor, partial(function, *args, **kwargs))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # アプリ起動時に LLM の preload とフォールバック音声の prewarm を走らせる
        try:
            await run_serialized(service.warmup)
            yield
        finally:
            await run_serialized(service.shutdown)
            service_executor.shutdown(wait=False, cancel_futures=True)

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
        return await run_serialized(service.create_session, payload)

    @app.post("/api/v1/game-events", response_model=AcceptedEventResponse)
    async def post_game_event(
        payload: GameEvent,
        authorization: Annotated[str | None, Header()] = None,
        x_dogido_session_id: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Response:
        _ensure_authorized(resolved_settings, authorization)
        def process_and_dispatch():
            result = service.process_event(
                payload,
                session_id=x_dogido_session_id,
                idempotency_key=idempotency_key,
            )
            # 順序を崩さないため、dispatchまで同じ直列worker内で完了させる。
            if result.actions:
                service.dispatch_actions(result.actions)
            return result

        result = await run_serialized(process_and_dispatch)

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
        def process_and_dispatch():
            result, actions = service.process_batch(
                payload.events,
                session_id=x_dogido_session_id,
            )
            if actions:
                service.dispatch_actions(actions)
            return result

        result = await run_serialized(process_and_dispatch)
        return result

    @app.post("/api/v1/adapter-sessions/{session_id}/heartbeat", response_model=HeartbeatResponse)
    async def post_heartbeat(
        session_id: str,
        payload: HeartbeatRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> HeartbeatResponse:
        # アダプタが生きているかの死活確認と、最後に受け取ったシーケンス番号の記録
        _ensure_authorized(resolved_settings, authorization)
        def heartbeat() -> HeartbeatResponse:
            if session_id not in service.sessions:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="unknown session_id",
                )
            return service.heartbeat(session_id, payload.last_sequence)

        return await run_serialized(heartbeat)

    @app.delete("/api/v1/adapter-sessions/{session_id}", response_model=CloseSessionResponse)
    async def delete_session(
        session_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> CloseSessionResponse:
        # アダプタ正常終了時にセッションを明示クローズする
        # 異常終了時はハートビートのタイムアウトで検知する想定
        _ensure_authorized(resolved_settings, authorization)
        return await run_serialized(service.close_session, session_id)

    @app.post("/api/v1/player-input")
    async def post_player_input(
        payload: PlayerInputRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        # 音声入力（dogido_server.voice_input）やテスト用 curl からの話しかけ。
        # 次のゲームイベントの user_text としてチャットと同じ経路に合流する
        _ensure_authorized(resolved_settings, authorization)
        return await run_serialized(
            service.push_player_input,
            payload.text,
            source=payload.source,
        )

    @app.get("/api/v1/voice-input/context", response_model=VoiceInputContextResponse)
    async def get_voice_input_context(
        authorization: Annotated[str | None, Header()] = None,
    ) -> VoiceInputContextResponse:
        # voice_input は別プロセスなので、書き起こし直前にworkshop状態だけを取得する。
        _ensure_authorized(resolved_settings, authorization)
        return await run_serialized(service.voice_input_context)

    @app.post("/api/v1/training-feedback", response_model=TrainingFeedbackResponse)
    async def post_training_feedback(
        payload: TrainingFeedbackRequest,
        authorization: Annotated[str | None, Header()] = None,
        x_dogido_session_id: Annotated[str | None, Header()] = None,
    ) -> TrainingFeedbackResponse:
        # Fabricの評価キーから来る明示signal。直前応答はservice側のRAM snapshotを使い、
        # クライアントに本文やゲーム状況を再送させない。
        _ensure_authorized(resolved_settings, authorization)
        return await run_serialized(
            service.submit_training_feedback,
            x_dogido_session_id,
            payload,
        )

    @app.get("/api/v1/memory/haiku")
    async def get_haiku_memory(
        authorization: Annotated[str | None, Header()] = None,
    ) -> list[dict[str, object]]:
        _ensure_authorized(resolved_settings, authorization)
        return await run_serialized(service.list_haiku_memory)

    @app.get("/api/v1/memory/profile")
    async def get_memory_profile(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _ensure_authorized(resolved_settings, authorization)
        return await run_serialized(service.memory_profile)

    @app.get("/api/v1/memory/summary")
    async def get_memory_summary(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _ensure_authorized(resolved_settings, authorization)
        return await run_serialized(service.memory_startup_summary)

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
