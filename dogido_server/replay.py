from __future__ import annotations

import argparse
import json
from pathlib import Path

from dogido_server.config import get_settings
from dogido_server.models import BatchEventRequest, GameEvent
from dogido_server.service import DogidoService


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay Dogido event fixtures")
    parser.add_argument("paths", nargs="+", help="Event JSON files or directories")
    parser.add_argument("--session-id", default="replay_session")
    parser.add_argument("--no-audio", action="store_true")
    args = parser.parse_args()

    settings = get_settings().model_copy(update={"audio_enabled": not args.no_audio})
    service = DogidoService(settings)

    for path in _expand_paths(args.paths):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "events" in payload:
            batch = BatchEventRequest.model_validate(payload)
            response, actions = service.process_batch(batch.events, session_id=args.session_id)
            if actions:
                service.dispatch_actions(actions)
            print(f"{path.name}: processed={response.processed} deduplicated={response.deduplicated}")
            continue

        event = GameEvent.model_validate(payload)
        result = service.process_event(event, session_id=args.session_id)
        print(
            f"{path.name}: mode={result.response.state.mode if result.response.state else 'n/a'} "
            f"panic={bool(result.response.outputs and result.response.outputs.panic_cue_enqueued)} "
            f"callout={bool(result.response.outputs and result.response.outputs.callout_enqueued)} "
            f"speech={bool(result.response.outputs and result.response.outputs.speech_enqueued)}"
        )
        for action in result.actions:
            print(f"  - {action.layer}: {action.text or action.cue_id}")
        if result.actions:
            service.dispatch_actions(result.actions)


def _expand_paths(raw_paths: list[str]) -> list[Path]:
    expanded: list[Path] = []
    for raw in raw_paths:
        path = Path(raw)
        if path.is_dir():
            expanded.extend(sorted(path.glob("*.json")))
        else:
            expanded.append(path)
    return expanded


if __name__ == "__main__":
    main()
