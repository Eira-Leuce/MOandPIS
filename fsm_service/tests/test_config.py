"""Загрузка конфигурации: шаблон, пользователи, каталог системы."""

import shutil

import pytest

from fsm_service import (
    ConfigurationError,
    load_directory,
    load_directory_yaml,
    load_system_dir,
    load_template,
)

from conftest import SPEC, V3


def test_template_from_yaml():
    engine = load_system_dir(SPEC)
    t = engine.template_for("drawing")
    assert (t.id, t.title, t.initial) == ("document_approval", "Утверждение документа", "preparation")
    assert t.create_roles == {"developer"}
    assert t.state("normcontrol").roles == {"normcontroller"} and t.state("approved").final
    assert t.state("preparation").title == "Подготовка"
    assert len(t.transitions("normcontrol", "accept")) == 2


def test_directory():
    users = load_directory_yaml(SPEC / "users.yaml")
    assert users.user("sidorov").roles == {"chief_designer", "normcontroller"}
    assert users.role("developer").title == "Разработчик"


@pytest.mark.parametrize("data, message", [
    ({"roles": [{"id": "a"}, {"id": "a"}]}, "уже существует"),
    ({"roles": [{"id": "a"}], "users": [{"id": "u", "roles": []}]}, "хотя бы одна роль"),
    ({"roles": [{"id": "a"}], "users": [{"id": "u", "roles": ["b"]}]}, "неизвестные роли"),
])
def test_directory_errors(data, message):
    with pytest.raises(ConfigurationError, match=message):
        load_directory(data)


BASE = {"states": [{"id": "a", "initial": True}, {"id": "b", "final": True}],
        "transitions": [{"from": "a", "action": "go", "to": "b"}]}


@pytest.mark.parametrize("change, message", [
    ({"states": [{"id": "a"}]}, "не задано начальное"),
    ({"states": [{"id": "a", "initial": True}, {"id": "b", "initial": True}]}, "несколько"),
    ({"transitions": [{"from": "a", "action": "go", "to": "x"}]}, "неизвестное состояние"),
    ({"transitions": [{"from": "a", "to": "b"}]}, "from, action и to"),
    ({"actions": [{"id": "other"}]}, "не объявлено в actions"),
    ({"handlers": {"go": "nope"}}, "не передан"),
    ({"states": [{"id": "a", "initial": True}, {"id": "b", "final": True, "roles": ["x"]}]}, "роли не нужны"),
])
def test_template_errors(change, message):
    with pytest.raises(ConfigurationError, match=message):
        load_template({**BASE, **change})


def test_handlers_bound_by_name():
    calls = []
    t = load_template({**BASE, "handlers": {"go": "log"}}, {"log": lambda s, m: calls.append(m.event)})
    from fsm_service import ProcessInstance
    ProcessInstance(template=t).handle("go")
    assert calls == ["go"]


def test_system_dir_requires_files(tmp_path):
    with pytest.raises(ConfigurationError, match="classes.yaml"):
        load_system_dir(tmp_path)
    shutil.copy(V3 / "classes.yaml", tmp_path)
    shutil.copy(V3 / "objects.yaml", tmp_path)
    with pytest.raises(ConfigurationError, match="нет шаблонов"):
        load_system_dir(tmp_path)


def test_variant3_dir_loads():
    engine = load_system_dir(V3)
    assert engine.template_for("standard_order") is not None
    assert len(engine.objects.objects) == 15 and engine.processes == ()
