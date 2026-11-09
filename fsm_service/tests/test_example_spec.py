"""Пример раздела 1 задания (examples/document_approval — конфигурация без изменений):
таблица выполнения раздела 1.5 и отдельная демонстрация ДНФ."""

import pytest

TABLE = [
    # № пользователь объект команда результат состояние-после фрагмент-причины
    (1, "ivanov", "drw-1", "start", "accepted", "preparation", None),
    (2, "ivanov", "drw-1", "submit", "accepted", "normcontrol", None),
    (3, "petrov", "drw-1", "accept", "accepted", "approval", None),   # 12 листов, normal
    (4, "petrov", "drw-1", "approve", "rejected", "approval", "chief_designer"),
    (5, "sidorov", "drw-1", "approve", "accepted", "approved", None),
    (6, "ivanov", "drw-too-large", "start", "accepted", "preparation", None),
    (7, "ivanov", "drw-too-large", "submit", "rejected", "preparation", "sheets_in_range"),  # 30 > 20
    (8, "ivanov", "drw-small", "start", "accepted", "preparation", None),
    (9, "ivanov", "drw-small", "submit", "accepted", "normcontrol", None),
    (10, "petrov", "drw-small", "accept", "accepted", "approved", None),  # не более 2 листов
    (11, "ivanov", "drw-incomplete", "start", "accepted", "preparation", None),
    (12, "ivanov", "drw-incomplete", "submit", "rejected", "preparation", "gearboxes_complete"),  # 1 шестерня
]


def run(session, user, obj, command):
    return session.start(user, obj) if command == "start" else session.act(user, obj, command)


def test_execution_table(spec):
    for no, user, obj, command, result, state, reason in TABLE:
        entry = run(spec, user, obj, command)
        assert entry["result"] == result, f"строка {no}: {entry['reason']}"
        assert entry["state_after"] == state == spec.state(obj), f"строка {no}"
        if reason:
            assert reason in entry["reason"], f"строка {no}: {entry['reason']}"
    assert len(spec.log.entries) == 12  # одна команда — одна запись журнала


@pytest.mark.parametrize("obj, after_accept, finish", [
    ("drw-simplified", "approved", None),       # 5 листов, simplified: вторая группа ДНФ
    ("drw-mandatory", "approval", "approved"),   # 2 листа, mandatory: несмотря на малое число листов
])
def test_dnf_demo(spec, obj, after_accept, finish):
    spec.start("ivanov", obj)
    spec.act("ivanov", obj, "submit")
    assert spec.act("petrov", obj, "accept")["state_after"] == after_accept
    if finish:
        assert spec.act("sidorov", obj, "approve")["state_after"] == finish
    assert all(e["result"] == "accepted" for e in spec.log.entries)


def test_dnf_demo_totals(spec):
    """«Всего получится 7 успешных команд и 7 записей лога»."""
    for obj in ("drw-simplified", "drw-mandatory"):
        spec.start("ivanov", obj)
        spec.act("ivanov", obj, "submit")
        spec.act("petrov", obj, "accept")
    spec.act("sidorov", "drw-mandatory", "approve")
    assert len(spec.log.entries) == 7 and all(e["result"] == "accepted" for e in spec.log.entries)


def test_inheritance_in_example(spec):
    """drw-too-large: у design_document максимум 50, у чертежа (переопределён) — 20."""
    obj = spec.engine.objects.get("drw-too-large")
    assert obj.registry.parameters("design_document")["sheets"].max == 50
    assert obj.get_range("sheets") == (1, 20)


def test_composition_in_example(spec):
    """drw-incomplete → gb-2 → одна шестерня при минимуме 2: путь gearboxes.gears с all ложен."""
    gb2 = spec.engine.objects.get("gb-2")
    assert gb2.get_range("gears") == (2, 6) and len(gb2.members("gears")) == 1
    # у drw-small состава нет — all для пустого состава истинен, submit проходит (строка 9)
    assert spec.engine.objects.get("drw-small").values["gearboxes"] == []
