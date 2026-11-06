"""Классы объектов: описание типов данными (паттерн Type Object, раздел 5.1 задания).

Класс: идентификатор, название, необязательный родитель, параметры.
Параметр: идентификатор, название, тип и ограничения:
  * скалярный — тип integer, decimal или string; у числового необязательные
    min и max; необязательное значение по умолчанию default;
  * состав — тип равен идентификатору класса объектов: значение — список
    идентификаторов объектов этого класса (или его подклассов);
    необязательные границы количества quantity_min и quantity_max.

Наследование: у класса не более одного родителя; подкласс получает параметры
родителя, может добавлять новые и полностью заменять описание параметра с тем
же идентификатором, сохраняя тип. Наследование реализовано над данными, а не
через подклассы языка программирования.
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .errors import ConfigurationError

SCALAR_TYPES = frozenset({"integer", "decimal", "string"})


class _Missing:
    """Маркер «значение по умолчанию не задано» (в отличие от default: null)."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


def matches_type(value: Any, type_name: str) -> bool:
    """None (незаданное значение) допустим для любого скалярного типа."""
    if value is None:
        return True
    if isinstance(value, bool):  # bool в Python — подкласс int, но числом не считаем
        return False
    if type_name == "integer":
        return isinstance(value, int)
    if type_name == "decimal":
        return isinstance(value, Real)
    if type_name == "string":
        return isinstance(value, str)
    raise ValueError(f"неизвестный тип параметра {type_name!r}")


def _is_number(x: Any) -> bool:
    return isinstance(x, Real) and not isinstance(x, bool)


@dataclass(frozen=True)
class ParameterDef:
    id: str
    title: str
    type: str  # integer | decimal | string | <id класса> для состава
    min: Real | None = None
    max: Real | None = None
    default: Any = MISSING
    quantity_min: int | None = None
    quantity_max: int | None = None

    @property
    def is_composition(self) -> bool:
        return self.type not in SCALAR_TYPES

    @property
    def has_default(self) -> bool:
        return self.default is not MISSING

    def bounds(self) -> tuple[Real | None, Real | None]:
        """Диапазон для in_range: min–max у числа, quantity_min–quantity_max у состава."""
        if self.is_composition:
            return self.quantity_min, self.quantity_max
        return self.min, self.max

    def __post_init__(self) -> None:
        where = f"параметр {self.id!r}"
        if self.is_composition:
            if self.min is not None or self.max is not None:
                raise ConfigurationError(f"{where}: у состава границы количества — quantity_min/quantity_max")
            if self.has_default:
                raise ConfigurationError(f"{where}: у состава нет значения по умолчанию (пропущенный — пустой список)")
            for name in ("quantity_min", "quantity_max"):
                value = getattr(self, name)
                if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                    raise ConfigurationError(f"{where}: {name} должен быть неотрицательным целым")
            low, high = self.quantity_min, self.quantity_max
        else:
            if self.quantity_min is not None or self.quantity_max is not None:
                raise ConfigurationError(f"{where}: quantity_min/quantity_max допустимы только у состава")
            numeric = self.type in ("integer", "decimal")
            for name in ("min", "max"):
                value = getattr(self, name)
                if value is None:
                    continue
                if not numeric:
                    raise ConfigurationError(f"{where}: {name} допустим только у числового параметра")
                if not _is_number(value):
                    raise ConfigurationError(f"{where}: {name} должен быть числом")
            if self.has_default and not matches_type(self.default, self.type):
                raise ConfigurationError(f"{where}: default {self.default!r} не соответствует типу {self.type}")
            low, high = self.min, self.max
        if low is not None and high is not None and low > high:
            raise ConfigurationError(f"{where}: нижняя граница больше верхней")


@dataclass(frozen=True)
class ObjectClass:
    id: str
    title: str = ""
    parent: str | None = None
    own_parameters: tuple[ParameterDef, ...] = ()


class ClassRegistry:
    """Описания классов объектов; объединяет параметры с учётом родителей."""

    def __init__(self) -> None:
        self._classes: dict[str, ObjectClass] = {}

    def add_class(
        self,
        class_id: str,
        title: str = "",
        parent: str | None = None,
        parameters: Iterable[ParameterDef] = (),
    ) -> ObjectClass:
        if not class_id:
            raise ConfigurationError("идентификатор класса не может быть пустым")
        if class_id in SCALAR_TYPES:
            raise ConfigurationError(f"имя класса {class_id!r} совпадает с именем скалярного типа")
        if class_id in self._classes:
            raise ConfigurationError(f"класс {class_id!r} уже существует")
        if parent is not None and parent not in self._classes:
            raise ConfigurationError(f"класс {class_id!r}: неизвестный родитель {parent!r}")
        parameters = tuple(parameters)
        seen: set[str] = set()
        inherited = self.parameters(parent) if parent else {}
        for p in parameters:
            if "." in p.id:
                raise ConfigurationError(f"класс {class_id!r}: точка в имени параметра {p.id!r} зарезервирована для путей")
            if p.id in seen:
                raise ConfigurationError(f"класс {class_id!r}: параметр {p.id!r} описан дважды")
            seen.add(p.id)
            base = inherited.get(p.id)
            if base is not None and base.type != p.type:
                raise ConfigurationError(
                    f"класс {class_id!r}: переопределённый параметр {p.id!r} меняет тип "
                    f"{base.type} → {p.type} (тип менять нельзя)"
                )
        cls = ObjectClass(class_id, title, parent, parameters)
        self._classes[class_id] = cls
        return cls

    def check_composition_types(self) -> None:
        """Тип каждого состава — существующий класс. Вызывается после загрузки всех классов."""
        for cls in self._classes.values():
            for p in cls.own_parameters:
                if p.is_composition and p.type not in self._classes:
                    raise ConfigurationError(
                        f"класс {cls.id!r}, параметр {p.id!r}: тип {p.type!r} — не скалярный тип и не класс"
                    )

    def get(self, class_id: str) -> ObjectClass:
        try:
            return self._classes[class_id]
        except KeyError:
            raise ConfigurationError(f"неизвестный класс {class_id!r}") from None

    def __contains__(self, class_id: object) -> bool:
        return class_id in self._classes

    @property
    def classes(self) -> tuple[ObjectClass, ...]:
        return tuple(self._classes.values())

    def lineage(self, class_id: str) -> list[str]:
        """Класс и его предки, от самого класса к корню."""
        result = []
        current: str | None = class_id
        while current is not None:
            result.append(current)
            current = self.get(current).parent
        return result

    def is_subclass(self, class_id: str, ancestor: str) -> bool:
        """class_id совпадает с ancestor или является его потомком."""
        return ancestor in self.lineage(class_id)

    def parameters(self, class_id: str) -> dict[str, ParameterDef]:
        """Параметры класса с учётом наследования: подкласс заменяет одноимённые."""
        result: dict[str, ParameterDef] = {}
        for cid in reversed(self.lineage(class_id)):
            for p in self.get(cid).own_parameters:
                result[p.id] = p
        return result


def parse_parameter(data: Mapping[str, Any]) -> ParameterDef:
    if "id" not in data or "type" not in data:
        raise ConfigurationError(f"у параметра должны быть id и type: {dict(data)}")
    return ParameterDef(
        data["id"], data.get("title", data["id"]), data["type"],
        data.get("min"), data.get("max"),
        data["default"] if "default" in data else MISSING,
        data.get("quantity_min"), data.get("quantity_max"),
    )


def load_classes(data: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> ClassRegistry:
    """Принимает {"classes": [...]} или сам список. Порядок в списке не важен:
    родители добавляются раньше потомков автоматически."""
    items = list(data.get("classes") or []) if isinstance(data, Mapping) else list(data)
    for c in items:
        if "id" not in c:
            raise ConfigurationError(f"у класса нет id: {c}")
    pending = {c["id"]: c for c in items}
    if len(pending) != len(items):
        raise ConfigurationError("в списке классов есть повторяющиеся id")
    registry = ClassRegistry()
    while pending:
        ready = [c for c in pending.values() if c.get("parent") is None or c["parent"] in registry]
        if not ready:
            raise ConfigurationError(f"неизвестный родитель или цикл наследования: {sorted(pending)}")
        for c in ready:
            params = [parse_parameter(p) for p in c.get("parameters") or []]
            registry.add_class(c["id"], c.get("title", ""), c.get("parent"), params)
            del pending[c["id"]]
    registry.check_composition_types()
    return registry


def load_classes_yaml(path: str | Path) -> ClassRegistry:
    with open(path, encoding="utf-8") as f:
        return load_classes(yaml.safe_load(f))
