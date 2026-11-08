"""Исполнитель: шаблон по точному классу, запуск процесса над готовым объектом, действия."""

import pytest

from fsm_service import (
    ConfigurationError,
    ProcessEngine,
    load_classes_yaml,
    load_directory_yaml,
    load_objects_yaml,
    load_template,
    load_template_yaml,
)

from conftest import SPEC


@pytest.fixture
def engine():
    classes = load_classes_yaml(SPEC / "classes.yaml")
    users = load_directory_yaml(SPEC / "users.yaml")
    e = ProcessEngine(classes, load_objects_yaml(SPEC / "objects.yaml", classes), users)
    e.add_template(load_template_yaml(SPEC / "document_approval.yaml", directory=users))
    return e


def minimal(process, class_id, roles=("developer",)):
    return {"process": process, "class": class_id, "create_roles": list(roles),
            "states": [{"id": "draft", "initial": True, "roles": list(roles)}, {"id": "done", "final": True}],
            "transitions": [{"from": "draft", "action": "finish", "to": "done"}]}


# --- выбор шаблона ----------------------------------------------------------------------

def test_template_bound_to_exact_class(engine):
    assert engine.template_for("drawing").id == "document_approval"
    assert engine.template_for("design_document") is None
    assert engine.template_for("diagram") is None  # шаблоны не наследуются


def test_subclass_object_without_own_template_cannot_start(engine):
    engine.objects.add("dgm-1", "diagram", {"designation": "СХ"})
    r = engine.start("ivanov", "dgm-1")
    assert not r.accepted and "нет шаблона" in r.reason and engine.process("dgm-1") is None


def test_parent_template_does_not_apply_to_subclass(engine):
    """Даже если шаблон есть у родителя, у подкласса его нет."""
    engine.add_template(load_template(minimal("doc_flow", "design_document"), directory=engine.directory))
    assert engine.template_for("diagram") is None
    assert engine.template_for("drawing").id == "document_approval"


def test_only_one_template_per_class(engine):
    with pytest.raises(ConfigurationError, match="уже есть шаблон"):
        engine.add_template(load_template(minimal("dup", "drawing"), directory=engine.directory))


@pytest.mark.parametrize("cfg, message", [
    (minimal("x", "ghost"), "неизвестный класс"),
    ({**minimal("x", "gear"), "class": None}, "не привязан"),
    (minimal("x", "gear", roles=("admin",)), "неизвестные роли"),
])
def test_bad_templates(engine, cfg, message):
    with pytest.raises(ConfigurationError, match=message):
        engine.add_template(load_template(cfg))


def test_template_with_foreign_directory(engine):
    other = load_directory_yaml(SPEC / "users.yaml")
    with pytest.raises(ConfigurationError, match="другим справочником"):
        engine.add_template(load_template(minimal("x", "gear"), directory=other))


# --- start ---------------------------------------------------------------------------------

def test_start_sets_initial_state(engine):
    r = engine.start("ivanov", "drw-1")
    assert r.accepted and (r.state_before, r.state_after) == (None, "preparation")
    assert engine.process("drw-1").state == "preparation"


def test_object_loaded_is_not_process_started(engine):
    assert "drw-1" in engine.objects and engine.process("drw-1") is None


@pytest.mark.parametrize("user, obj, message", [
    ("petrov", "drw-1", "требуется роль developer"),
    ("hacker", "drw-1", "неизвестный пользователь"),
    ("ivanov", "ghost", "неизвестный объект"),
    ("ivanov", "gear-1", "нет шаблона"),  # объект состава: процесс над ним не запускается
])
def test_start_rejections(engine, user, obj, message):
    r = engine.start(user, obj)
    assert not r.accepted and message in r.reason
    assert engine.processes == ()


def test_repeated_start_rejected_with_current_state(engine):
    engine.start("ivanov", "drw-1")
    engine.act("ivanov", "drw-1", "submit")
    r = engine.start("ivanov", "drw-1")
    assert not r.accepted and "уже запущен" in r.reason
    assert (r.state_before, r.state_after) == ("normcontrol", "normcontrol")


# --- action --------------------------------------------------------------------------------

def test_action_before_start_rejected(engine):
    r = engine.act("ivanov", "drw-1", "submit")
    assert not r.accepted and "не запущен" in r.reason and r.state_before is None


@pytest.mark.parametrize("user, obj, message", [("hacker", "drw-1", "неизвестный пользователь"),
                                                ("ivanov", "ghost", "неизвестный объект")])
def test_action_unknown_user_or_object(engine, user, obj, message):
    engine.start("ivanov", "drw-1")
    r = engine.act(user, obj, "submit")
    assert not r.accepted and message in r.reason and engine.process("drw-1").state == "preparation"


def test_action_not_provided_and_final(engine):
    engine.start("ivanov", "drw-small")
    assert "не предусмотрено" in engine.act("ivanov", "drw-small", "approve").reason
    engine.act("ivanov", "drw-small", "cancel")
    r = engine.act("ivanov", "drw-small", "submit")
    assert not r.accepted and "завершён" in r.reason and r.state_after == "cancelled"


def test_independent_instances_share_template(engine):
    """Шаблон общий, состояние — у каждого экземпляра своё (задел для 3-1-2)."""
    engine.start("ivanov", "drw-1")
    engine.start("ivanov", "drw-small")
    engine.act("ivanov", "drw-1", "submit")
    a, b = engine.process("drw-1"), engine.process("drw-small")
    assert a.template is b.template and (a.state, b.state) == ("normcontrol", "preparation")


def test_ambiguous_transition_is_error_and_keeps_state(engine):
    """Специально подготовленный шаблон: условия двух переходов выполняются одновременно."""
    cfg = minimal("amb", "gear")
    cfg["predicates"] = [{"id": "many", "parameter": "teeth", "operator": ">", "value": 10},
                         {"id": "some", "parameter": "teeth", "operator": ">", "value": 0}]
    cfg["states"].append({"id": "other", "final": True})
    cfg["transitions"] = [
        {"name": "to_done", "from": "draft", "action": "finish", "to": "done", "condition": [["many"]]},
        {"name": "to_other", "from": "draft", "action": "finish", "to": "other", "condition": [["some"]]},
    ]
    engine.add_template(load_template(cfg, directory=engine.directory))
    engine.start("ivanov", "gear-1")  # 20 зубьев: оба условия истинны
    r = engine.act("ivanov", "gear-1", "finish")
    assert r.status == "error" and "неоднозначный" in r.reason
    assert "to_done" in r.reason and "to_other" in r.reason
    assert engine.process("gear-1").state == "draft"
