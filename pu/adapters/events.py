"""Small OOP event publisher adapters around the existing observer callbacks."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from pu.core.interfaces.events import EventPublisher


class CallableEventPublisher(EventPublisher):
    def __init__(self, callback: Callable[[str, dict], None] | None):
        self.callback = callback

    def publish(self, kind: str, event: dict) -> None:
        if self.callback is not None:
            self.callback(kind, event)


class CompositeEventPublisher(EventPublisher):
    def __init__(self, publishers: Iterable[EventPublisher] = ()):
        self.publishers = [p for p in publishers if p is not None]

    def publish(self, kind: str, event: dict) -> None:
        for publisher in self.publishers:
            publisher.publish(kind, event)
