"""Классы объектов: иерархия, наследование и переопределение параметров, default, составы."""

import pytest

from fsm_service import MISSING, ClassRegistry, ConfigurationError, ParameterDef, load_classes, load_classes_yaml

from conftest import SPEC, V3


@pytest.fixture
def classes():
    return load_classes_yaml(SPEC / "classes.yaml")


def test_hierarchy(classes):
    assert classes.lineage("drawing") == ["drawing", "design_document"]
    assert classes.is_subclass("drawing", "design_document")
    assert classes.is_subclass("drawing", "drawing")
    assert not classes.is_subclass("design_document", "drawing")
    assert not classes.is_subclass("drawing", "diagram")


def test_parameters_inherited_unchanged(classes):
    diagram = classes.parameters("diagram")
    assert set(diagram) == {"designation", "sheets", "gearboxes"}
    assert (diagram["sheets"].min, diagram["sheets"].max) == (1, 50)


def test_parameter_overridden_and_added(classes):
    drawing = classes.parameters("drawing")
    assert set(drawing) == {"designation", "sheets", "gearboxes", "mass", "review_mode"}
    assert (drawing["sheets"].min, drawing["sheets"].max) == (1, 20)  # переопределён
    assert drawing["mass"].type == "decimal" and not drawing["mass"].has_default  # добавлен
    assert classes.parameters("design_document")["sheets"].max == 50  # родитель не тронут


def test_variant3_override(tmp_path):
    reg = load_classes_yaml(V3 / "classes.yaml")
    base, sub = reg.parameters("supply_order"), reg.parameters("standard_order")
    assert (base["delivery_days"].max, base["delivery_days"].default) == (180, 30)
    assert (sub["delivery_days"].max, sub["delivery_days"].default) == (60, 14)
    assert "contract" in sub and "contract" not in base
    assert sub["items"] is base["items"]  # состав унаследован без изменений


def test_override_cannot_change_type(classes):
    with pytest.raises(ConfigurationError, match="меняет тип"):
        classes.add_class("bad", parent="design_document", parameters=[ParameterDef("sheets", "Листы", "decimal")])
    with pytest.raises(ConfigurationError, match="меняет тип"):
        classes.add_class("bad2", parent="design_document", parameters=[ParameterDef("gearboxes", "Р", "gear")])


def test_deep_hierarchy_nearest_override_wins():
    reg = ClassRegistry()
    reg.add_class("a", parameters=[ParameterDef("x", "X", "integer", 0, 100)])
    reg.add_class("b", parent="a", parameters=[ParameterDef("x", "X", "integer", 0, 10)])
    reg.add_class("c", parent="b")
    assert reg.parameters("c")["x"].max == 10
    assert reg.lineage("c") == ["c", "b", "a"]


def test_default_and_missing(classes):
    p = classes.parameters("drawing")
    assert p["sheets"].default == 1 and p["review_mode"].default == "normal"
    assert p["mass"].default is MISSING


def test_composition_parameter(classes):
    gears = classes.parameters("gearbox")["gears"]
    assert gears.is_composition and gears.type == "gear"
    assert gears.bounds() == (2, 6)
    assert classes.parameters("gearbox")["bearings"].bounds() == (2, None)  # верхняя граница не задана
    assert classes.parameters("gear")["teeth"].bounds() == (10, 200)


@pytest.mark.parametrize("param, message", [
    ({"id": "x", "type": "string", "min": 1}, "только у числового"),
    ({"id": "x", "type": "integer", "min": 5, "max": 1}, "больше верхней"),
    ({"id": "x", "type": "integer", "min": "1"}, "должен быть числом"),
    ({"id": "x", "type": "integer", "default": "1"}, "не соответствует типу"),
    ({"id": "x", "type": "integer", "quantity_min": 1}, "только у состава"),
    ({"id": "x", "type": "c", "min": 1}, "quantity_min/quantity_max"),
    ({"id": "x", "type": "c", "default": []}, "нет значения по умолчанию"),
    ({"id": "x", "type": "c", "quantity_min": -1}, "неотрицательным"),
    ({"id": "x"}, "id и type"),
    ({"id": "a.b", "type": "string"}, "точка"),
])
def test_bad_parameter(param, message):
    with pytest.raises(ConfigurationError, match=message):
        load_classes([{"id": "c", "parameters": [param]}])


@pytest.mark.parametrize("data, message", [
    ([{"id": "a"}, {"id": "a"}], "повторяющиеся"),
    ([{"id": "a", "parent": "ghost"}], "неизвестный родитель"),
    ([{"id": "a", "parent": "b"}, {"id": "b", "parent": "a"}], "цикл"),
    ([{"title": "без id"}], "нет id"),
    ([{"id": "a", "parameters": [{"id": "x", "type": "ghost"}]}], "не скалярный тип и не класс"),
    ([{"id": "integer"}], "скалярного типа"),
])
def test_bad_classes(data, message):
    with pytest.raises(ConfigurationError, match=message):
        load_classes(data)


def test_order_in_file_does_not_matter():
    reg = load_classes({"classes": [{"id": "child", "parent": "root"}, {"id": "root"}]})
    assert reg.lineage("child") == ["child", "root"]
