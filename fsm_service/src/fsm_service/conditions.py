"""Предикаты и условия переходов в ДНФ (разделы 2.2, 5.3, 5.4 задания).

Условие из конфигурации компилируется в дерево выражений (паттерн Interpreter):
    [[a, b], [not c]]  →  Or(And(a, b), And(Not(c)))
Дерево строится один раз при загрузке шаблона, значения проверяются заново
при каждом вычислении. Переход без условия получает выражение Always.

Предикат параметра — parameter, operator и, для сравнения, value:
  * filled — значение задано (пустая строка задана, пустой состав — нет);
  * =, != — для любых типов; <, <=, >, >= — для чисел и составов;
  * in_range — значение в диапазоне класса объекта: min–max у числа,
    quantity_min–quantity_max у состава; незаданная граница не проверяется.
  У состава во всех сравнениях и в in_range используется количество.
  Незаданное значение (в том числе параметр, которого нет у класса объекта)
  делает предикат ложным, включая filled.

Путь «состав.параметр» проверяет параметр каждого объекта состава (один
уровень) и объединяет результаты агрегатором, выбранным по полю aggregate
(паттерн Strategy): all — для каждого (для пустого состава истинно),
any — хотя бы для одного (для пустого состава ложно). Агрегаторы
регистрируются в реестре «имя → функция»; новый добавляется регистрацией,
без изменения вычисления предикатов и обработчика переходов.

Предикат класса — объект принадлежит классу или его подклассу.
"""

from __future__ import annotations

import operator as op
from abc import ABC, abstractmethod
from dataclasses import dataclass
from numbers import Real
from typing import Any, Callable, Iterable, Mapping, Protocol

from .classes import ParameterDef
from .errors import ConfigurationError

# ---------------------------------------------------------------------------
# Контекст вычисления — объект, над которым проверяются условия
# ---------------------------------------------------------------------------


class EvaluationContext(Protocol):
    def parameter(self, name: str) -> ParameterDef | None:
        """Описание параметра у класса объекта (с учётом наследования) или None."""

    def get_value(self, name: str) -> Any:
        """Значение параметра (None — не задано; у состава — список id)."""

    def members(self, name: str) -> list["EvaluationContext"] | None:
        """Объекты состава name; None, если такого состава у класса нет."""

    def is_instance_of(self, class_id: str) -> bool:
        """Объект принадлежит классу class_id или его подклассу."""


class ConditionError(Exception):
    """Условие нельзя вычислить: например, числовой оператор над строкой."""


# ---------------------------------------------------------------------------
# Агрегаторы (Strategy)
# ---------------------------------------------------------------------------

Aggregator = Callable[[Iterable[bool]], bool]

AGGREGATORS: dict[str, Aggregator] = {
    "all": all,  # для пустой последовательности — True
    "any": any,  # для пустой последовательности — False
}


def register_aggregator(name: str, function: Aggregator) -> None:
    """Добавить агрегатор: «последовательность булевых значений → булево значение»."""
    if not callable(function):
        raise ConfigurationError(f"агрегатор {name!r} не вызываемый")
    AGGREGATORS[name] = function


def get_aggregator(name: str) -> Aggregator:
    try:
        return AGGREGATORS[name]
    except KeyError:
        known = ", ".join(sorted(AGGREGATORS))
        raise ConfigurationError(f"неподдерживаемый агрегатор {name!r} (есть: {known})") from None


# ---------------------------------------------------------------------------
# Выражения (Interpreter)
# ---------------------------------------------------------------------------


class Expression(ABC):
    @abstractmethod
    def eval(self, ctx: EvaluationContext) -> bool: ...

    def false_leaves(self, ctx: EvaluationContext) -> list[str]:
        """Ложные листья (предикаты, в том числе с not) — для объяснения отказа."""
        return [] if self.eval(ctx) else [str(self)]


class Always(Expression):
    """Условие перехода без condition: разрешён всегда."""

    def eval(self, ctx: EvaluationContext) -> bool:
        return True

    def __str__(self) -> str:
        return "true"


@dataclass(frozen=True)
class Not(Expression):
    inner: Expression

    def eval(self, ctx: EvaluationContext) -> bool:
        return not self.inner.eval(ctx)

    def __str__(self) -> str:
        return f"not {self.inner}"


@dataclass(frozen=True)
class And(Expression):
    items: tuple[Expression, ...]

    def eval(self, ctx: EvaluationContext) -> bool:
        return all(item.eval(ctx) for item in self.items)

    def false_leaves(self, ctx: EvaluationContext) -> list[str]:
        return [leaf for item in self.items for leaf in item.false_leaves(ctx)]

    def __str__(self) -> str:
        return "(" + " and ".join(map(str, self.items)) + ")"


@dataclass(frozen=True)
class Or(Expression):
    items: tuple[Expression, ...]

    def eval(self, ctx: EvaluationContext) -> bool:
        return any(item.eval(ctx) for item in self.items)

    def satisfied(self, ctx: EvaluationContext) -> list[int]:
        """Номера истинных дизъюнктов (для тестов и демонстрации)."""
        return [i for i, item in enumerate(self.items) if item.eval(ctx)]

    def false_leaves(self, ctx: EvaluationContext) -> list[str]:
        if self.eval(ctx):
            return []
        return sorted({leaf for item in self.items for leaf in item.false_leaves(ctx)})

    def __str__(self) -> str:
        return " or ".join(map(str, self.items))


# ---------------------------------------------------------------------------
# Предикаты — листья дерева
# ---------------------------------------------------------------------------

_COMPARE = {"=": op.eq, "!=": op.ne, "<": op.lt, "<=": op.le, ">": op.gt, ">=": op.ge}
ORDER_OPERATORS = frozenset({"<", "<=", ">", ">="})
PARAMETER_OPERATORS = frozenset({"filled", "in_range", *_COMPARE})


def _is_number(x: Any) -> bool:
    return isinstance(x, Real) and not isinstance(x, bool)


@dataclass(frozen=True)
class ParamPredicate(Expression):
    id: str
    path: str  # «параметр» или «состав.параметр»
    operator: str
    value: Any = None
    aggregate: str | None = None

    def __post_init__(self) -> None:
        where = f"предикат {self.id!r}"
        if self.operator not in PARAMETER_OPERATORS:
            raise ConfigurationError(f"{where}: неизвестный оператор {self.operator!r}")
        needs_value = self.operator in _COMPARE
        if needs_value and self.value is None:
            raise ConfigurationError(f"{where}: для оператора {self.operator!r} нужно value")
        if not needs_value and self.value is not None:
            raise ConfigurationError(f"{where}: оператор {self.operator!r} не использует value")
        if self.operator in ORDER_OPERATORS and not _is_number(self.value):
            raise ConfigurationError(f"{where}: оператор {self.operator!r} требует числового value")
        parts = self.path.split(".")
        if len(parts) > 2 or not all(parts):
            raise ConfigurationError(f"{where}: путь {self.path!r} — «параметр» или «состав.параметр»")
        if len(parts) == 2 and self.aggregate is None:
            raise ConfigurationError(f"{where}: для пути {self.path!r} обязателен aggregate")
        if len(parts) == 1 and self.aggregate is not None:
            raise ConfigurationError(f"{where}: aggregate задаётся только для пути «состав.параметр»")
        if self.aggregate is not None:
            get_aggregator(self.aggregate)  # неизвестное имя — понятная ошибка при загрузке

    def eval(self, ctx: EvaluationContext) -> bool:
        if "." not in self.path:
            return self._check(ctx, self.path)
        composition, name = self.path.split(".")
        members = ctx.members(composition)
        if members is None:  # у класса объекта нет такого состава
            return False
        return get_aggregator(self.aggregate)(self._check(m, name) for m in members)

    def _check(self, ctx: EvaluationContext, name: str) -> bool:
        param = ctx.parameter(name)
        if param is None:  # параметра нет у класса объекта
            return False
        value = ctx.get_value(name)
        if param.is_composition:
            value = len(value or [])
            if self.operator == "filled":
                return value > 0
        else:
            if value is None:
                return False
            if self.operator == "filled":
                return True
            if (self.operator in ORDER_OPERATORS or self.operator == "in_range") and not _is_number(value):
                raise ConditionError(f"предикат {self.id!r}: {name} = {value!r} не число")
        if self.operator == "in_range":
            low, high = param.bounds()
            return (low is None or value >= low) and (high is None or value <= high)
        return _COMPARE[self.operator](value, self.value)

    def __str__(self) -> str:
        return self.id


@dataclass(frozen=True)
class ClassPredicate(Expression):
    id: str
    class_id: str

    def eval(self, ctx: EvaluationContext) -> bool:
        return ctx.is_instance_of(self.class_id)

    def __str__(self) -> str:
        return self.id


Predicate = ParamPredicate | ClassPredicate


# ---------------------------------------------------------------------------
# Разбор конфигурации
# ---------------------------------------------------------------------------


def parse_predicate(data: Mapping[str, Any]) -> Predicate:
    pid = data.get("id")
    if not pid:
        raise ConfigurationError(f"у предиката нет id: {dict(data)}")
    has_param, has_class = "parameter" in data, "class" in data
    if has_param == has_class:
        raise ConfigurationError(f"предикат {pid!r}: нужно ровно одно из parameter / class")
    if has_class:
        return ClassPredicate(pid, data["class"])
    if "operator" not in data:
        raise ConfigurationError(f"предикат {pid!r}: нет operator")
    return ParamPredicate(pid, data["parameter"], str(data["operator"]), data.get("value"), data.get("aggregate"))


def parse_predicates(items: Iterable[Mapping[str, Any]]) -> dict[str, Predicate]:
    result: dict[str, Predicate] = {}
    for item in items or ():
        predicate = parse_predicate(item)
        if predicate.id in result:
            raise ConfigurationError(f"предикат {predicate.id!r} описан дважды")
        result[predicate.id] = predicate
    return result


def _literal(text: str, predicates: Mapping[str, Predicate]) -> Expression:
    tokens = str(text).split()
    negated = bool(tokens) and tokens[0] == "not"
    if len(tokens) != (2 if negated else 1):
        raise ConfigurationError(f"не могу разобрать литерал {text!r}: ожидается 'id' или 'not id'")
    pid = tokens[-1]
    if pid not in predicates:
        raise ConfigurationError(f"в условии использован неизвестный предикат {pid!r}")
    return Not(predicates[pid]) if negated else predicates[pid]


def compile_condition(data: Any, predicates: Mapping[str, Predicate]) -> Expression:
    """Список дизъюнктов (ИЛИ), дизъюнкт — список литералов (И) → дерево выражений."""
    if data is None:
        return Always()
    if not isinstance(data, list) or not data:
        raise ConfigurationError(f"условие должно быть непустым списком дизъюнктов, получено {data!r}")
    disjuncts = []
    for conj in data:
        items = [conj] if isinstance(conj, str) else conj
        if not isinstance(items, list) or not items:
            raise ConfigurationError(f"дизъюнкт должен быть непустым списком литералов: {conj!r}")
        disjuncts.append(And(tuple(_literal(x, predicates) for x in items)))
    return Or(tuple(disjuncts))
