"""Transient, read-only presentation of actual poem generation, never workshop lifetime."""
from contextlib import contextmanager
from functools import wraps
import logging


LOGGER = logging.getLogger(__name__)


def _publish(machine):
    observer = getattr(machine, "haiku_presentation_observer", None)
    if observer is not None:
        try:
            observer()
        except Exception:
            # A HUD failure must never cancel a poem or mask a generation error.
            LOGGER.exception("haiku_presentation_publish_failed")


@contextmanager
def thinking_pose(machine):
    depth = getattr(machine, "haiku_thinking_depth", 0)
    machine.haiku_thinking_depth = depth + 1
    if depth == 0:
        _publish(machine)
    try:
        yield
    finally:
        machine.haiku_thinking_depth = depth
        if depth == 0:
            # Executed before the caller can enqueue returned speech, also on failure.
            _publish(machine)


def during_haiku_generation(function):
    @wraps(function)
    def wrapped(machine, *args, **kwargs):
        with thinking_pose(machine):
            return function(machine, *args, **kwargs)
    return wrapped
