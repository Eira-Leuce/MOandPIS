"""Исполнитель процессов: запуск процесса над готовым объектом и выполнение действий.

Три понятия (раздел «Объём работы» задания):
  * объект (ProcessObject) — параметры предметной области, загружен из конфигурации;
  * шаблон процесса (ProcessTemplate) — общие правила, привязан к точному классу;
  * экземпляр процесса (ProcessInstance) — связывает объект с шаблоном и хранит текущее
    состояние. Загруженный объект ещё не означает запущенный процесс.

Правила:
  * у класса не более одного шаблона; поле class шаблона точно совпадает с классом
    объекта — шаблоны не наследуются, даже при наследовании параметров;
  * start: пользователь и объект существуют, для класса объекта есть шаблон,
    у пользователя есть роль запуска (create_roles), процесс ещё не запущен;
  * action: пользователь и объект существуют, процесс запущен и не завершён;
    дальше — проверки экземпляра (роль состояния, переход, условие).
Отказ не меняет состояние. Рабочие данные — только в памяти.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .classes import ClassRegistry
from .errors import ConfigurationError
from .objects import ObjectStore
from .service import ACCEPTED, REJECTED, ProcessInstance, ProcessTemplate
from .users import Directory


@dataclass(frozen=True)
class CommandResult:
    """Результат команды start или action — то, что попадает в лог."""

    status: str  # accepted | rejected | error
    user: str | None
    object: str | None
    state_before: str | None
    state_after: str | None
    reason: str | None = None
    result: Any = None  # то, что вернул хэндлер действия

    @property
    def accepted(self) -> bool:
        return self.status == ACCEPTED


class ProcessEngine:
    def __init__(self, classes: ClassRegistry, objects: ObjectStore, directory: Directory | None = None) -> None:
        self.classes = classes
        self.objects = objects
        self.directory = directory
        self._templates: dict[str, ProcessTemplate] = {}  # класс -> шаблон
        self._processes: dict[str, ProcessInstance] = {}  # id объекта -> экземпляр процесса

    # --- шаблоны ----------------------------------------------------------------------
    def add_template(self, template: ProcessTemplate) -> ProcessTemplate:
        if template.class_id is None:
            raise ConfigurationError(f"шаблон {template.id!r} не привязан к классу (поле class)")
        self.classes.get(template.class_id)  # класс должен существовать
        if template.class_id in self._templates:
            raise ConfigurationError(
                f"у класса {template.class_id!r} уже есть шаблон {self._templates[template.class_id].id!r}"
            )
        if self.directory is not None:
            if template.directory is not None and template.directory is not self.directory:
                raise ConfigurationError(f"шаблон {template.id!r} загружен с другим справочником пользователей")
            self.directory.check_roles(template.create_roles, f"шаблон {template.id!r}, create_roles")
            for state in template.states:
                self.directory.check_roles(state.roles, f"шаблон {template.id!r}, состояние {state.name!r}")
            template.directory = self.directory
        self._templates[template.class_id] = template
        return template

    def template_for(self, class_id: str) -> ProcessTemplate | None:
        """Шаблон, непосредственно привязанный к классу (без поиска у предков)."""
        return self._templates.get(class_id)

    @property
    def templates(self) -> tuple[ProcessTemplate, ...]:
        return tuple(self._templates.values())

    # --- процессы ---------------------------------------------------------------------
    def process(self, object_id: str) -> ProcessInstance | None:
        return self._processes.get(object_id)

    @property
    def processes(self) -> tuple[ProcessInstance, ...]:
        return tuple(self._processes.values())

    def _unknown_user(self, user: str | None) -> bool:
        return self.directory is not None and self.directory.user(user) is None

    def start(self, user: str | None, object_id: str | None) -> CommandResult:
        def reject(reason: str, state: str | None = None) -> CommandResult:
            return CommandResult(REJECTED, user, object_id, state, state, reason)

        if self._unknown_user(user):
            return reject(f"неизвестный пользователь {user!r}")
        obj = self.objects.find(object_id) if object_id is not None else None
        if obj is None:
            return reject(f"неизвестный объект {object_id!r}")
        running = self._processes.get(object_id)
        if running is not None:
            return reject(f"процесс над объектом {object_id!r} уже запущен", running.state)
        template = self.template_for(obj.class_id)
        if template is None:
            return reject(f"для класса {obj.class_id!r} нет шаблона процесса")
        if self.directory is not None:
            u = self.directory.user(user)
            if not u.has_any_role(template.create_roles):
                need = ", ".join(sorted(template.create_roles)) or "—"
                return reject(f"для запуска требуется роль {need}")
        instance = ProcessInstance(template=template, context=obj, object_id=object_id)
        self._processes[object_id] = instance
        return CommandResult(ACCEPTED, user, object_id, None, instance.state, None)

    def act(self, user: str | None, object_id: str | None, action: str) -> CommandResult:
        def reject(reason: str, state: str | None = None) -> CommandResult:
            return CommandResult(REJECTED, user, object_id, state, state, reason)

        if self._unknown_user(user):
            return reject(f"неизвестный пользователь {user!r}")
        if object_id is None or object_id not in self.objects:
            return reject(f"неизвестный объект {object_id!r}")
        instance = self._processes.get(object_id)
        if instance is None:
            return reject(f"процесс над объектом {object_id!r} не запущен")
        r = instance.handle(action, sender=user)
        return CommandResult(r.status, user, object_id, r.source, r.target, r.reason, r.result)
