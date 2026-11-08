"""Команды и JSON-журнал: каждая команда — одна запись; успех, отказ, ошибка."""

import io
import json
import re

import pytest

from fsm_service import Action, CommandError, JsonLinesLog, MemoryLog, ProcessService, Start, parse_command

FIELDS = {"time", "command", "user", "object", "state_before", "state_after", "result", "reason"}


def test_parse_commands():
    assert parse_command({"type": "start", "user": "u", "object": "o"}) == Start("u", "o")
    assert parse_command('{"type": "action", "user": "u", "object": "o", "action": "a"}') == Action("u", "o", "a")


@pytest.mark.parametrize("message, text", [
    ("не json", "не JSON"),
    ("[1, 2]", "JSON-объектом"),
    ({"type": "delete", "user": "u", "object": "o"}, "неизвестный тип"),
    ({"type": "action", "user": "u", "object": "o"}, "нет полей ['action']"),
    ({"type": "start", "user": "", "object": "o"}, "нет полей ['user']"),
    ({"type": "start", "user": "u", "object": 5}, "нет полей ['object']"),
    ({"type": "start", "user": "u", "object": "o", "extra": 1}, "лишние поля"),
])
def test_bad_commands(message, text):
    with pytest.raises(CommandError, match=re.escape(text)):
        parse_command(message)


def test_successful_start_logged(spec):
    entry = spec.start("ivanov", "drw-1")
    assert set(entry) == FIELDS
    assert entry["command"] == {"type": "start", "user": "ivanov", "object": "drw-1"}
    assert (entry["user"], entry["object"]) == ("ivanov", "drw-1")
    assert (entry["state_before"], entry["state_after"], entry["result"], entry["reason"]) == \
           (None, "preparation", "accepted", None)
    assert spec.log.entries == [entry]


def test_rejection_logged_and_state_unchanged(spec):
    spec.start("ivanov", "drw-1")
    spec.act("ivanov", "drw-1", "submit")
    spec.act("petrov", "drw-1", "accept")
    entry = spec.act("petrov", "drw-1", "approve")
    assert entry["result"] == "rejected" and "chief_designer" in entry["reason"]
    assert entry["state_before"] == entry["state_after"] == "approval" == spec.state("drw-1")


def test_repeated_start_logs_current_state(spec):
    spec.start("ivanov", "drw-1")
    entry = spec.start("ivanov", "drw-1")
    assert entry["result"] == "rejected" and entry["state_before"] == entry["state_after"] == "preparation"


def test_unknown_object_logged_with_nulls(spec):
    entry = spec.start("ivanov", "ghost")
    assert entry["result"] == "rejected" and entry["object"] == "ghost"
    assert entry["state_before"] is None and entry["state_after"] is None


@pytest.mark.parametrize("message", ["{битый json", {"type": "action", "user": "ivanov", "object": "drw-1"},
                                     {"type": "fly"}, 42])
def test_malformed_command_gets_error_entry(spec, message):
    entry = spec.service.send(message)
    assert entry["result"] == "error" and entry["reason"]
    assert spec.log.entries == [entry]


def test_json_string_command_is_logged_parsed(spec):
    entry = spec.service.send('{"type": "start", "user": "ivanov", "object": "drw-1"}')
    assert entry["command"] == {"type": "start", "user": "ivanov", "object": "drw-1"}


def test_internal_error_is_logged_not_raised(spec, monkeypatch):
    def boom(*args):
        raise RuntimeError("сбой")
    monkeypatch.setattr(spec.engine, "start", boom)
    entry = spec.start("ivanov", "drw-1")
    assert entry["result"] == "error" and "сбой" in entry["reason"]


def test_jsonl_file_one_entry_per_line(spec, tmp_path):
    path = tmp_path / "log.jsonl"
    log = JsonLinesLog.open(path)
    service = ProcessService(spec.engine, log)
    service.send({"type": "start", "user": "ivanov", "object": "drw-1"})
    service.send({"type": "action", "user": "petrov", "object": "drw-1", "action": "submit"})
    log.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    entries = [json.loads(line) for line in lines]
    assert [e["result"] for e in entries] == ["accepted", "rejected"]
    assert entries[1]["reason"] == "требуется роль developer (у пользователя 'petrov' её нет)"


def test_log_is_injected_dependency(spec):
    stream = io.StringIO()
    service = ProcessService(spec.engine, JsonLinesLog(stream))
    service.send({"type": "start", "user": "ivanov", "object": "drw-1"})
    assert json.loads(stream.getvalue())["state_after"] == "preparation"
    assert isinstance(spec.log, MemoryLog) and spec.log.entries == []  # журнал сессии не тронут
