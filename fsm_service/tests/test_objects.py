"""Готовые объекты: значения по умолчанию, явный null, составы, ссылки."""

import pytest

from fsm_service import ConfigurationError, load_classes_yaml, load_objects, load_objects_yaml

from conftest import SPEC, V3


@pytest.fixture
def classes():
    return load_classes_yaml(SPEC / "classes.yaml")


@pytest.fixture
def store(classes):
    return load_objects_yaml(SPEC / "objects.yaml", classes)


def test_default_applied_when_field_omitted(store):
    assert store.get("gear-1").values["module"] == 2          # module по умолчанию
    assert store.get("gear-2").values["module"] == 2.5        # задан явно
    assert store.get("bearing-1").values["designation"] == "6204"
    assert store.get("drw-incomplete").values["sheets"] == 1  # sheets пропущен → default 1
    assert store.get("drw-1").values["review_mode"] == "normal"


def test_no_default_means_unset(store):
    assert store.get("gear-3").values["teeth"] is None        # поле пропущено, default нет
    assert store.get("drw-1").values["mass"] is None


def test_explicit_null_is_not_replaced_by_default(classes):
    store = load_objects([{"id": "d", "class": "drawing", "values": {"sheets": None}}], classes)
    assert store.get("d").values["sheets"] is None            # default 1 не подставлен


def test_composition_omitted_is_empty(store, classes):
    assert store.get("drw-small").values["gearboxes"] == []
    explicit_null = load_objects([{"id": "d", "class": "drawing", "values": {"gearboxes": None}}], classes)
    assert explicit_null.get("d").values["gearboxes"] == []


def test_bounds_not_checked_on_load(store):
    assert store.get("drw-too-large").values["sheets"] == 30  # максимум 20 — проверит предикат


def test_members(store):
    gb = store.get("gb-1")
    assert [m.id for m in gb.members("gears")] == ["gear-1", "gear-2"]
    assert gb.members("ratio") is None                        # не состав
    assert gb.members("ghost") is None
    assert store.get("drw-1").members("gearboxes")[0].id == "gb-1"


def test_object_context(store):
    drw = store.get("drw-1")
    assert drw.get_range("sheets") == (1, 20)
    assert drw.get_range("gearboxes") == (None, 4)
    assert drw.is_instance_of("design_document") and not drw.is_instance_of("diagram")
    assert not drw.is_instance_of("no_such_class")
    assert drw.parameter("ratio") is None


def test_variant3_defaults():
    classes = load_classes_yaml(V3 / "classes.yaml")
    store = load_objects_yaml(V3 / "objects.yaml", classes)
    assert store.get("ord-default-delivery").values["delivery_days"] == 14  # default подкласса
    assert store.get("base-order").values["delivery_days"] == 30            # default базового класса
    assert store.get("ord-1").values["priority"] == "normal"
    assert store.get("ord-no-terms").values["payment_days"] is None         # явный null
    assert store.get("item-washer").values["quantity"] == 1


@pytest.mark.parametrize("objects, message", [
    ([{"id": "x", "class": "gear"}, {"id": "x", "class": "gear"}], "дважды"),
    ([{"id": "x", "class": "ghost"}], "неизвестный класс"),
    ([{"id": "x", "class": "gear", "values": {"colour": "red"}}], "нет параметров"),
    ([{"id": "x", "class": "gear", "values": {"teeth": "20"}}], "не соответствует типу"),
    ([{"id": "x", "class": "gearbox", "values": {"gears": "gear-1"}}], "список идентификаторов"),
    ([{"id": "x", "class": "gearbox", "values": {"gears": ["nope"]}}], "нет объекта"),
    ([{"id": "b", "class": "bearing"}, {"id": "x", "class": "gearbox", "values": {"gears": ["b"]}}], "нужен 'gear'"),
    ([{"class": "gear"}], "нет id"),
    ([{"id": "x"}], "не указан класс"),
])
def test_bad_objects(classes, objects, message):
    with pytest.raises(ConfigurationError, match=message):
        load_objects(objects, classes)


def test_composition_accepts_subclass_objects(classes):
    """Состав gearboxes: design_document → можно ссылаться и на объекты подклассов (здесь — свой класс)."""
    store = load_objects([
        {"id": "g", "class": "gear", "values": {"teeth": 20}},
        {"id": "gb", "class": "gearbox", "values": {"gears": ["g", "g"]}},
        {"id": "d", "class": "diagram", "values": {"gearboxes": ["gb"]}},
    ], classes)
    assert len(store.get("gb").members("gears")) == 2
