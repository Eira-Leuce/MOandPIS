"""Готовые объекты предметной области (objects.yaml).

Объекты описаны заранее и загружаются в память при каждом запуске программы;
создавать и редактировать их во время работы не требуется.

Правила загрузки значений:
  * поле указано — берётся как есть, в том числе явный null (значение не задано);
  * скалярное поле пропущено — подставляется default параметра, если он есть,
    иначе значение не задано (None);
  * состав пропущен — пустой список.
Границы min/max и quantity_min/quantity_max при загрузке не проверяются:
их проверяют предикаты переходов (in_range).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Real
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .classes import ClassRegistry, ParameterDef, matches_type
from .errors import ConfigurationError


@dataclass
class ProcessObject:
    """Объект: класс и фактические значения параметров.

    Реализует контекст вычисления предикатов (conditions.EvaluationContext):
    значения, описание параметра (для диапазона), объекты состава, класс.
    """

    id: str
    class_id: str
    registry: ClassRegistry
    values: dict[str, Any] = field(default_factory=dict)
    store: "ObjectStore | None" = field(default=None, repr=False, compare=False)

    def parameter(self, name: str) -> ParameterDef | None:
        return self.registry.parameters(self.class_id).get(name)

    def get_value(self, name: str) -> Any:
        return self.values.get(name)

    def get_range(self, name: str) -> tuple[Real | None, Real | None]:
        p = self.parameter(name)
        return p.bounds() if p else (None, None)

    def members(self, name: str) -> list["ProcessObject"] | None:
        """Объекты состава name или None, если такого состава у класса нет."""
        p = self.parameter(name)
        if p is None or not p.is_composition:
            return None
        if self.store is None:
            raise ConfigurationError(f"объект {self.id!r} не связан с хранилищем объектов")
        return [self.store.get(i) for i in self.values.get(name) or []]

    def is_instance_of(self, class_id: str) -> bool:
        return class_id in self.registry and self.registry.is_subclass(self.class_id, class_id)


class ObjectStore:
    """Все загруженные объекты в памяти: id → объект."""

    def __init__(self, registry: ClassRegistry) -> None:
        self.registry = registry
        self._objects: dict[str, ProcessObject] = {}

    def add(self, object_id: str, class_id: str, values: Mapping[str, Any] | None = None) -> ProcessObject:
        if not object_id:
            raise ConfigurationError("у объекта нет id")
        if object_id in self._objects:
            raise ConfigurationError(f"объект {object_id!r} описан дважды")
        params = self.registry.parameters(self.registry.get(class_id).id)
        given = dict(values or {})
        unknown = sorted(set(given) - params.keys())
        if unknown:
            raise ConfigurationError(f"объект {object_id!r}: у класса {class_id!r} нет параметров {unknown}")
        result: dict[str, Any] = {}
        for name, p in params.items():
            if name in given:
                value = given[name]  # явный null остаётся незаданным
            elif p.is_composition:
                value = []
            else:
                value = p.default if p.has_default else None
            if p.is_composition:
                value = [] if value is None else value
                if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
                    raise ConfigurationError(f"объект {object_id!r}: состав {name!r} — список идентификаторов")
            elif not matches_type(value, p.type):
                raise ConfigurationError(
                    f"объект {object_id!r}: значение {value!r} не соответствует типу {p.type} параметра {name!r}"
                )
            result[name] = value
        obj = ProcessObject(object_id, class_id, self.registry, result, self)
        self._objects[object_id] = obj
        return obj

    def check_references(self) -> None:
        """Ссылки составов указывают на существующие объекты подходящего класса."""
        for obj in self._objects.values():
            for name, p in self.registry.parameters(obj.class_id).items():
                if not p.is_composition:
                    continue
                for ref in obj.values[name]:
                    target = self._objects.get(ref)
                    if target is None:
                        raise ConfigurationError(f"объект {obj.id!r}, состав {name!r}: нет объекта {ref!r}")
                    if not self.registry.is_subclass(target.class_id, p.type):
                        raise ConfigurationError(
                            f"объект {obj.id!r}, состав {name!r}: {ref!r} класса {target.class_id!r}, "
                            f"а нужен {p.type!r}"
                        )

    def get(self, object_id: str) -> ProcessObject:
        try:
            return self._objects[object_id]
        except KeyError:
            raise ConfigurationError(f"неизвестный объект {object_id!r}") from None

    def find(self, object_id: str) -> ProcessObject | None:
        return self._objects.get(object_id)

    def __contains__(self, object_id: object) -> bool:
        return object_id in self._objects

    @property
    def objects(self) -> tuple[ProcessObject, ...]:
        return tuple(self._objects.values())


def load_objects(data: Mapping[str, Any] | Iterable[Mapping[str, Any]], registry: ClassRegistry) -> ObjectStore:
    items = list(data.get("objects") or []) if isinstance(data, Mapping) else list(data)
    store = ObjectStore(registry)
    for o in items:
        if "class" not in o:
            raise ConfigurationError(f"у объекта не указан класс: {o}")
        store.add(o.get("id"), o["class"], o.get("values"))
    store.check_references()
    return store


def load_objects_yaml(path: str | Path, registry: ClassRegistry) -> ObjectStore:
    with open(path, encoding="utf-8") as f:
        return load_objects(yaml.safe_load(f) or {}, registry)
