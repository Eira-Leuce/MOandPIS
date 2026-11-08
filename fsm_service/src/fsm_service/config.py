"""Загрузка конфигурации: шаблоны процессов и сборка системы целиком.

Формат шаблона — раздел 1.4 задания:

    process: document_approval
    title: Утверждение документа
    class: drawing                 # точный класс объектов; шаблоны не наследуются
    create_roles: [developer]      # кто может запускать процесс
    actions: [{id: submit, title: Передать на проверку}, ...]
    predicates:
      - {id: has_designation, parameter: designation, operator: filled}
      - {id: gearboxes_complete, parameter: gearboxes.gears, aggregate: all, operator: in_range}
    states:
      - {id: preparation, title: Подготовка, initial: true, roles: [developer]}
      - {id: approved, final: true}
    transitions:
      - from: preparation
        action: submit
        to: normcontrol
        condition:
          - [has_designation, sheets_in_range]   # группа «И»; между группами — «ИЛИ»
    handlers:                      # необязательно: действие -> имя хэндлера (код)
      submit: notify

Каталог конфигурации (load_system_dir) содержит classes.yaml, objects.yaml,
users.yaml и файлы шаблонов (любой YAML с ключом process).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .classes import load_classes_yaml
from .conditions import compile_condition, parse_predicates
from .engine import ProcessEngine
from .errors import ConfigurationError
from .objects import load_objects_yaml
from .service import Handler, ProcessTemplate
from .users import Directory, load_directory_yaml


def _initial_state(config: Mapping[str, Any], states: list[Mapping[str, Any]]) -> str:
    flagged = [s["id"] for s in states if s.get("initial")]
    top = config.get("initial")
    if len(flagged) > 1:
        raise ConfigurationError(f"начальных состояний несколько: {flagged}")
    if top is not None and flagged and flagged[0] != top:
        raise ConfigurationError(f"начальное состояние задано по-разному: {top!r} и {flagged[0]!r}")
    initial = top if top is not None else (flagged[0] if flagged else None)
    if initial is None:
        raise ConfigurationError("не задано начальное состояние (initial: true у состояния)")
    if initial not in {s["id"] for s in states}:
        raise ConfigurationError(f"начальное состояние {initial!r} не описано в states")
    return initial


def load_template(
    config: Mapping[str, Any],
    handlers: Mapping[str, Handler] | None = None,
    directory: Directory | None = None,
) -> ProcessTemplate:
    handlers = handlers or {}
    if "states" not in config:
        raise ConfigurationError("в шаблоне нет обязательного ключа 'states'")
    states = list(config["states"])
    for s in states:
        if "id" not in s:
            raise ConfigurationError(f"у состояния нет id: {s}")
    initial = _initial_state(config, states)
    by_id = {s["id"]: s for s in states}

    def flags(state: Mapping[str, Any]) -> dict[str, Any]:
        return {"final": bool(state.get("final", False)), "roles": state.get("roles") or ()}

    actions = {}
    for a in config.get("actions") or []:
        if "id" not in a:
            raise ConfigurationError(f"у действия нет id: {a}")
        actions[a["id"]] = a.get("title", "")

    template = ProcessTemplate(
        initial,
        directory=directory,
        create_roles=config.get("create_roles") or (),
        id=config.get("process"),
        title=config.get("title", ""),
        class_id=config.get("class"),
        actions=actions,
        initial_title=by_id[initial].get("title", ""),
        **flags(by_id[initial]),
    )
    for s in states:
        if s["id"] != initial:
            template.add_state(s["id"], title=s.get("title", ""), **flags(s))

    predicates = parse_predicates(config.get("predicates") or [])
    template.predicates = dict(predicates)
    for t in config.get("transitions") or []:
        event = t.get("action", t.get("event"))
        if event is None or "from" not in t or "to" not in t:
            raise ConfigurationError(f"у перехода должны быть from, action и to: {t}")
        condition = compile_condition(t.get("condition"), predicates)
        template.add_transition(t["from"], event, t["to"], name=t.get("name"), condition=condition)

    for event, handler_name in (config.get("handlers") or {}).items():
        if handler_name not in handlers:
            raise ConfigurationError(f"хэндлер {handler_name!r} для действия {event!r} не передан")
        template.register_handler(event, handlers[handler_name])
    return template


def load_template_yaml(
    path: str | Path,
    handlers: Mapping[str, Handler] | None = None,
    directory: Directory | None = None,
) -> ProcessTemplate:
    with open(path, encoding="utf-8") as f:
        return load_template(yaml.safe_load(f), handlers, directory)


def load_system(
    classes: str | Path,
    objects: str | Path,
    users: str | Path | None,
    templates: Iterable[str | Path],
    handlers: Mapping[str, Handler] | None = None,
) -> ProcessEngine:
    """Собрать систему из файлов: классы, готовые объекты, пользователи, шаблоны."""
    registry = load_classes_yaml(classes)
    store = load_objects_yaml(objects, registry)
    directory = load_directory_yaml(users) if users else None
    engine = ProcessEngine(registry, store, directory)
    for path in templates:
        engine.add_template(load_template_yaml(path, handlers, directory))
    return engine


def load_system_dir(path: str | Path, handlers: Mapping[str, Handler] | None = None) -> ProcessEngine:
    """Каталог: classes.yaml, objects.yaml, users.yaml + шаблоны (YAML с ключом process)."""
    root = Path(path)
    for name in ("classes.yaml", "objects.yaml"):
        if not (root / name).exists():
            raise ConfigurationError(f"в каталоге {root} нет {name}")
    templates = []
    for p in sorted(root.glob("*.yaml")):
        if p.name in ("classes.yaml", "objects.yaml", "users.yaml"):
            continue
        with open(p, encoding="utf-8") as f:
            if isinstance(data := yaml.safe_load(f), dict) and "process" in data:
                templates.append(p)
    if not templates:
        raise ConfigurationError(f"в каталоге {root} нет шаблонов процессов")
    users = root / "users.yaml"
    return load_system(root / "classes.yaml", root / "objects.yaml", users if users.exists() else None,
                       templates, handlers)
