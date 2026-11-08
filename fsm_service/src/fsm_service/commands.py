"""Команды и журнал (разделы 2.3, 2.4, 5.5, 5.6 задания).

Команда — запрос с параметрами (паттерн Command): Start запускает процесс над
объектом, Action выполняет действие. Названия действий — данные Action, а не
отдельные классы. ProcessService разбирает сообщение, исполняет команду через
ProcessEngine и в одной точке пишет результат каждой команды в журнал —
успех, отказ и ошибку, включая некорректную команду.

Журнал — интерфейс AuditLog; JsonLinesLog пишет JSONL (одна запись — одна
строка JSON), MemoryLog хранит записи в памяти для тестов. Журнал передаётся
сервису через конструктор (внедрение зависимости).

Запись журнала:
    {"time": ..., "command": {...}, "user": ..., "object": ...,
     "state_before": ..., "state_after": ..., "result": "accepted|rejected|error",
     "reason": ...}
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TextIO

from .engine import CommandResult, ProcessEngine
from .service import ERROR


class CommandError(Exception):
    """Сообщение нельзя превратить в команду: неверный формат, нет обязательных полей."""


class Command(ABC):
    @abstractmethod
    def execute(self, engine: ProcessEngine) -> CommandResult: ...


@dataclass(frozen=True)
class Start(Command):
    user: str
    object_id: str

    def execute(self, engine: ProcessEngine) -> CommandResult:
        return engine.start(self.user, self.object_id)


@dataclass(frozen=True)
class Action(Command):
    user: str
    object_id: str
    action: str

    def execute(self, engine: ProcessEngine) -> CommandResult:
        return engine.act(self.user, self.object_id, self.action)


_FIELDS = {"start": ("user", "object"), "action": ("user", "object", "action")}


def parse_command(message: Any) -> Command:
    """Сообщение (dict или JSON-строка) → команда. Ошибка формата — CommandError."""
    if isinstance(message, str):
        try:
            message = json.loads(message)
        except json.JSONDecodeError as exc:
            raise CommandError(f"сообщение — не JSON: {exc.msg}") from None
    if not isinstance(message, dict):
        raise CommandError("сообщение должно быть JSON-объектом")
    kind = message.get("type")
    if kind not in _FIELDS:
        raise CommandError(f"неизвестный тип команды {kind!r} (есть: start, action)")
    missing = [f for f in _FIELDS[kind] if not isinstance(message.get(f), str) or not message.get(f)]
    if missing:
        raise CommandError(f"в команде {kind} нет полей {missing}")
    extra = sorted(set(message) - {"type", *_FIELDS[kind]})
    if extra:
        raise CommandError(f"в команде {kind} лишние поля {extra}")
    if kind == "start":
        return Start(message["user"], message["object"])
    return Action(message["user"], message["object"], message["action"])


# --- журнал -------------------------------------------------------------------------------


class AuditLog(Protocol):
    def write(self, entry: dict[str, Any]) -> None: ...


class MemoryLog:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def write(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)


class JsonLinesLog:
    """JSONL: одна запись — одна строка. Поток открывает и закрывает вызывающий код."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    @classmethod
    def open(cls, path: str | Path) -> "JsonLinesLog":
        return cls(open(path, "a", encoding="utf-8"))

    def write(self, entry: dict[str, Any]) -> None:
        self.stream.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        self.stream.flush()

    def close(self) -> None:
        self.stream.close()


class TeeLog:
    """Писать в несколько журналов сразу (например, файл и память)."""

    def __init__(self, *logs: AuditLog) -> None:
        self.logs = logs

    def write(self, entry: dict[str, Any]) -> None:
        for log in self.logs:
            log.write(entry)


# --- сервис -------------------------------------------------------------------------------


class ProcessService:
    """Принимает сообщения, исполняет команды и журналирует каждую."""

    def __init__(self, engine: ProcessEngine, audit: AuditLog) -> None:
        self.engine = engine
        self.audit = audit

    def send(self, message: Any) -> dict[str, Any]:
        """Обработать сообщение и вернуть запись журнала (она же — ответ вызывающему)."""
        try:
            command = parse_command(message)
        except CommandError as exc:
            result = CommandResult(ERROR, *_who(message), None, None, str(exc))
        else:
            try:
                result = command.execute(self.engine)
            except Exception as exc:  # непредвиденная ошибка — тоже в журнал
                result = CommandResult(ERROR, *_who(message), None, None, f"внутренняя ошибка: {exc}")
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "command": _as_logged(message),
            "user": result.user,
            "object": result.object,
            "state_before": result.state_before,
            "state_after": result.state_after,
            "result": result.status,
            "reason": result.reason,
        }
        self.audit.write(entry)
        return entry


def _as_logged(message: Any) -> Any:
    """Исходная команда для журнала: JSON-строку записываем разобранной."""
    if isinstance(message, str):
        try:
            return json.loads(message)
        except json.JSONDecodeError:
            return message
    return message


def _who(message: Any) -> tuple[str | None, str | None]:
    """Пользователь и объект из сообщения — если их удаётся определить."""
    if isinstance(message, str):
        try:
            message = json.loads(message)
        except json.JSONDecodeError:
            return None, None
    if not isinstance(message, dict):
        return None, None
    user, obj = message.get("user"), message.get("object")
    return (user if isinstance(user, str) else None), (obj if isinstance(obj, str) else None)
