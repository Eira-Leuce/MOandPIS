"""Предикаты и условия в ДНФ: операторы, диапазоны, незаданные значения, составы,
пути с агрегаторами, дерево выражений, ошибки описания."""

import pytest

from fsm_service import (
    AGGREGATORS,
    Always,
    And,
    ConditionError,
    ConfigurationError,
    Not,
    Or,
    compile_condition,
    get_aggregator,
    load_classes,
    load_objects,
    parse_predicates,
    register_aggregator,
)

CLASSES = load_classes([
    {"id": "part", "parameters": [
        {"id": "n", "type": "integer", "min": 1, "max": 10},
        {"id": "x", "type": "decimal"},
        {"id": "s", "type": "string"},
    ]},
    {"id": "box", "parameters": [
        {"id": "n", "type": "integer", "min": 1, "max": 20},
        {"id": "s", "type": "string"},
        {"id": "parts", "type": "part", "quantity_min": 2, "quantity_max": 3},
        {"id": "free", "type": "part"},
    ]},
    {"id": "big_box", "parent": "box", "parameters": [
        {"id": "n", "type": "integer", "min": 1, "max": 100},
    ]},
])


def objects(*items):
    return load_objects(list(items), CLASSES)


def pred(**data):
    data.setdefault("id", "p")
    return parse_predicates([data])["p"]


def box(values=None, parts=(), class_id="box"):
    items = [{"id": f"p{i}", "class": "part", "values": v} for i, v in enumerate(parts)]
    store = objects(*items, {"id": "b", "class": class_id,
                             "values": {**(values or {}), "parts": [f"p{i}" for i in range(len(parts))]}})
    return store.get("b")


# --- скалярные предикаты ----------------------------------------------------------

@pytest.mark.parametrize("operator, value, actual, expected", [
    ("=", 5, 5, True), ("=", 5, 6, False), ("=", "A", "A", True),
    ("!=", 5, 6, True), ("!=", "A", "A", False),
    ("<", 5, 4, True), ("<", 5, 5, False),
    ("<=", 5, 5, True), ("<=", 5, 6, False),
    (">", 5, 6, True), (">", 5, 5, False),
    (">=", 5, 5, True), (">=", 5, 4, False),
])
def test_comparison_operators(operator, value, actual, expected):
    parameter = "s" if isinstance(value, str) else "n"  # строки сравниваем по s, числа по n
    assert pred(parameter=parameter, operator=operator, value=value).eval(box({parameter: actual})) is expected


@pytest.mark.parametrize("class_id, n, expected", [
    ("box", 20, True), ("box", 21, False), ("box", 1, True), ("box", 0, False),
    ("big_box", 21, True), ("big_box", 100, True),  # диапазон переопределён в подклассе
])
def test_in_range_uses_class_of_object(class_id, n, expected):
    assert pred(parameter="n", operator="in_range").eval(box({"n": n}, class_id=class_id)) is expected


def test_in_range_open_bounds():
    store = objects({"id": "q", "class": "part", "values": {"x": -1e9}})
    assert pred(parameter="x", operator="in_range").eval(store.get("q"))  # у x нет границ


def test_unset_value_makes_predicate_false_including_filled():
    b = box()  # n и s не заданы
    for p in (pred(parameter="n", operator="filled"), pred(parameter="n", operator="in_range"),
              pred(parameter="n", operator="<", value=5), pred(parameter="s", operator="!=", value="A")):
        assert p.eval(b) is False
    assert Not(pred(parameter="n", operator="filled")).eval(b) is True  # not применяется обычным образом


def test_empty_string_is_filled():
    assert pred(parameter="s", operator="filled").eval(box({"s": ""}))


def test_parameter_absent_in_class_is_unset():
    part = objects({"id": "q", "class": "part"}).get("q")
    assert pred(parameter="parts", operator="filled").eval(part) is False  # у part нет состава parts
    assert pred(parameter="ghost", operator="=", value=1).eval(part) is False


def test_order_operator_on_string_is_error():
    with pytest.raises(ConditionError):
        pred(parameter="s", operator="<", value=1).eval(box({"s": "abc"}))


# --- составы ---------------------------------------------------------------------------

@pytest.mark.parametrize("count, filled, in_range, ge3", [
    (0, False, False, False), (1, True, False, False), (2, True, True, False),
    (3, True, True, True), (4, True, False, True),
])
def test_composition_uses_count(count, filled, in_range, ge3):
    b = box(parts=[{"n": 1}] * count)
    assert pred(parameter="parts", operator="filled").eval(b) is filled
    assert pred(parameter="parts", operator="in_range").eval(b) is in_range  # quantity 2..3
    assert pred(parameter="parts", operator=">=", value=3).eval(b) is ge3
    assert pred(parameter="parts", operator="=", value=count).eval(b)


def test_composition_without_bounds_in_range_true():
    store = objects({"id": "b", "class": "box"})
    assert pred(parameter="free", operator="in_range").eval(store.get("b"))


# --- пути и агрегаторы -----------------------------------------------------------------

@pytest.mark.parametrize("ns, all_ok, any_ok", [
    ([5, 7], True, True),
    ([5, 50], False, True),      # 50 вне диапазона part (1..10)
    ([50, 60], False, False),
    ([], True, False),           # пустой состав: all — истина, any — ложь
])
def test_path_with_aggregators(ns, all_ok, any_ok):
    b = box(parts=[{"n": n} for n in ns])
    assert pred(parameter="parts.n", aggregate="all", operator="in_range").eval(b) is all_ok
    assert pred(parameter="parts.n", aggregate="any", operator="in_range").eval(b) is any_ok


def test_path_in_range_uses_class_of_member():
    """У box тоже есть n (1..20), но для пути диапазон берётся у класса входящего объекта (part, 1..10)."""
    b = box(parts=[{"n": 15}, {"n": 15}])
    assert not pred(parameter="parts.n", aggregate="all", operator="in_range").eval(b)


def test_path_unset_member_value():
    b = box(parts=[{"n": 3}, {}])  # у второй части n не задано
    assert not pred(parameter="parts.n", aggregate="all", operator="filled").eval(b)
    assert pred(parameter="parts.n", aggregate="any", operator="filled").eval(b)


def test_path_over_non_composition_is_false():
    b = box({"n": 3})
    assert pred(parameter="n.x", aggregate="all", operator="filled").eval(b) is False


def test_new_aggregator_by_registration_only():
    """Добавление агрегатора — регистрация функции, без изменения вычисления предикатов."""
    register_aggregator("none", lambda results: not any(results))
    try:
        b = box(parts=[{"n": 50}, {"n": 60}])
        assert pred(parameter="parts.n", aggregate="none", operator="in_range").eval(b)
    finally:
        del AGGREGATORS["none"]


def test_unsupported_aggregator_is_clear_error():
    with pytest.raises(ConfigurationError, match="неподдерживаемый агрегатор 'most'"):
        pred(parameter="parts.n", aggregate="most", operator="filled")
    with pytest.raises(ConfigurationError, match="неподдерживаемый"):
        get_aggregator("most")


# --- предикат класса -------------------------------------------------------------------

def test_class_predicate_includes_subclasses():
    p = parse_predicates([{"id": "is_box", "class": "box"}])["is_box"]
    assert p.eval(box(class_id="big_box")) and p.eval(box())
    assert not p.eval(objects({"id": "q", "class": "part"}).get("q"))


# --- ДНФ -------------------------------------------------------------------------------

PREDS = parse_predicates([
    {"id": "a", "parameter": "n", "operator": "<=", "value": 2},
    {"id": "b", "parameter": "s", "operator": "=", "value": "simplified"},
    {"id": "c", "parameter": "s", "operator": "=", "value": "mandatory"},
])


def test_compile_builds_or_of_and():
    expr = compile_condition([["a", "b"], ["not c"]], PREDS)
    assert isinstance(expr, Or) and all(isinstance(x, And) for x in expr.items)
    assert isinstance(expr.items[1].items[0], Not)
    assert str(expr) == "(a and b) or (not c)"


def test_no_condition_is_always():
    assert isinstance(compile_condition(None, PREDS), Always)
    assert compile_condition(None, PREDS).eval(box())


@pytest.mark.parametrize("n, s, first, second", [
    (2, "normal", True, False),       # A и не C
    (5, "simplified", True, False),   # B и не C
    (2, "simplified", True, False),   # обе группы одного условия — это один переход
    (2, "mandatory", False, True),    # C
    (5, "normal", False, True),       # не A и не B
])
def test_dnf_table_from_assignment(n, s, first, second):
    """Таблица раздела 1.5: условия двух переходов accept взаимоисключающие (де Морган)."""
    to_approved = compile_condition([["a", "not c"], ["b", "not c"]], PREDS)
    to_approval = compile_condition([["c"], ["not a", "not b"]], PREDS)
    b = box({"n": n, "s": s})
    assert to_approved.eval(b) is first and to_approval.eval(b) is second


def test_each_disjunct_and_false_leaves():
    expr = compile_condition([["a"], ["b"]], PREDS)
    assert expr.satisfied(box({"n": 1, "s": "simplified"})) == [0, 1]
    assert expr.satisfied(box({"n": 9, "s": "simplified"})) == [1]
    assert expr.false_leaves(box({"n": 9, "s": "x"})) == ["a", "b"]
    assert compile_condition([["a", "not c"]], PREDS).false_leaves(box({"n": 1, "s": "mandatory"})) == ["not c"]


@pytest.mark.parametrize("predicate, message", [
    ({"parameter": "n", "operator": "filled"}, "нет id"),
    ({"id": "p", "operator": "filled"}, "ровно одно"),
    ({"id": "p", "parameter": "n", "class": "box", "operator": "filled"}, "ровно одно"),
    ({"id": "p", "parameter": "n", "operator": "~"}, "неизвестный оператор"),
    ({"id": "p", "parameter": "n", "operator": "="}, "нужно value"),
    ({"id": "p", "parameter": "n", "operator": "filled", "value": 1}, "не использует value"),
    ({"id": "p", "parameter": "n", "operator": "<", "value": "a"}, "числового"),
    ({"id": "p", "parameter": "parts.n", "operator": "filled"}, "обязателен aggregate"),
    ({"id": "p", "parameter": "n", "aggregate": "all", "operator": "filled"}, "только для пути"),
    ({"id": "p", "parameter": "a.b.c", "aggregate": "all", "operator": "filled"}, "путь"),
])
def test_bad_predicate(predicate, message):
    with pytest.raises(ConfigurationError, match=message):
        parse_predicates([predicate])


@pytest.mark.parametrize("data, message", [
    ([["unknown"]], "неизвестный предикат"),
    ([["not"]], "не могу разобрать"),
    ([["no a"]], "не могу разобрать"),
    ([[]], "непустым"),
    ([], "непустым списком"),
    ("a", "непустым списком"),
])
def test_bad_condition(data, message):
    with pytest.raises(ConfigurationError, match=message):
        compile_condition(data, PREDS)
