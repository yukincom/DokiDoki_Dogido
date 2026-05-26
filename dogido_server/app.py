from __future__ import annotations

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
    resolved_settings = settings or get_settings()
    service = DogidoService(resolved_settings)

    app = FastAPI(title=resolved_settings.service_name, version=resolved_settings.service_version)
    app.state.settings = resolved_settings
    app.state.service = service

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(_, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": exc.errors(),
                "body": exc.body,
            },
        )

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
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
        if result.actions:
            service.dispatch_actions(result.actions)

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
        _ensure_authorized(resolved_settings, authorization)
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
        _ensure_authorized(resolved_settings, authorization)
        if session_id not in service.sessions:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown session_id")
        return service.heartbeat(session_id, payload.last_sequence)

    @app.delete("/api/v1/adapter-sessions/{session_id}", response_model=CloseSessionResponse)
    async def delete_session(
        session_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> CloseSessionResponse:
        _ensure_authorized(resolved_settings, authorization)
        return service.close_session(session_id)

    return app


def _ensure_authorized(settings: Settings, authorization: str | None) -> None:
    if not settings.auth_token:
        return
    expected = f"Bearer {settings.auth_token}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "dogido_server.app:create_app",
        factory=True,
        host=settings.bind_host,
        port=settings.bind_port,
        reload=False,
    )


app = create_app()
