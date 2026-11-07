"""Шаблон процесса и экземпляр процесса — табличный конечный автомат (раздел 5.2).

Шаблон (ProcessTemplate) — общие правила: состояния с ролями, действия,
переходы «исходное состояние + действие → целевое состояние» с условием,
роли запуска (create_roles) и класс объектов. Шаблон не хранит текущее
состояние, поэтому один шаблон обслуживает любое число экземпляров.

Экземпляр (ProcessInstance) — конкретный запуск процесса над объектом: текущее
состояние и очередь сообщений; описание
автомата берётся из шаблона. Состояния, переходы и обработчики событий
(хэндлеры) регистрируются динамически, во время работы.

Обработка одного сообщения (действия); при любом отказе состояние не меняется:
  1. текущее состояние конечное                          → отказ;
  2. задан справочник пользователей, а у отправителя нет
     роли текущего состояния (или он неизвестен)          → отказ;
  3. по паре (состояние, действие) нет ни переходов,
     ни хэндлера                                          → отказ;
  4. из переходов отбираются те, чьё условие истинно:
       ни одного                                          → отказ;
       больше одного                                      → ошибка неоднозначного перехода;
  5. выполняется хэндлер (если зарегистрирован); исключение → ошибка;
  6. экземпляр переходит в целевое состояние (событие без переходов, но с
     хэндлером — внутреннее, состояние прежнее); результат команды пишет в журнал ProcessService.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .conditions import Always, EvaluationContext, Expression
from .errors import ConfigurationError
from .users import Directory, User

Guard = Callable[["ProcessInstance", "Message"], bool]
Handler = Callable[["ProcessInstance", "Message"], Any]

ACCEPTED, REJECTED, ERROR = "accepted", "rejected", "error"


@dataclass(frozen=True)
class Message:
    """Сообщение в очереди экземпляра: действие, данные, отправитель."""

    event: str
    payload: dict[str, Any] = field(default_factory=dict)
    sender: str | None = None


@dataclass(frozen=True)
class State:
    name: str
    final: bool = False
    roles: frozenset[str] = frozenset()  # кто выполняет действия в этом состоянии
    title: str = ""


@dataclass(frozen=True)
class Transition:
    source: str
    event: str
    target: str
    guard: Guard | None = None  # условие-функция (из кода)
    name: str | None = None  # для диагностики: какой переход сработал / конфликтует
    condition: Expression = field(default_factory=Always)  # условие из конфигурации

    def allows(self, service: "ProcessInstance", message: Message) -> bool:
        if not self.condition.eval(service.context):
            return False
        return self.guard is None or bool(self.guard(service, message))

    def label(self) -> str:
        return self.name or f"{self.source}->{self.target}"


@dataclass(frozen=True)
class ProcessResult:
    """Итог обработки одного сообщения."""

    message: Message
    status: str  # accepted | rejected | error
    source: str
    target: str  # при отказе и ошибке совпадает с source
    reason: str | None = None
    result: Any = None  # то, что вернул хэндлер

    @property
    def accepted(self) -> bool:
        return self.status == ACCEPTED

    @property
    def changed_state(self) -> bool:
        return self.accepted and self.source != self.target


class ProcessTemplate:
    """Шаблон процесса — описание автомата. Пополняется динамически."""

    def __init__(
        self,
        initial_state: str,
        *,
        final: bool = False,
        roles: Iterable[str] = (),
        directory: Directory | None = None,
        create_roles: Iterable[str] = (),
        id: str | None = None,
        title: str = "",
        class_id: str | None = None,
        actions: Mapping[str, str] | None = None,
        initial_title: str = "",
    ) -> None:
        self.id = id
        self.title = title
        self.class_id = class_id  # точный класс объектов, к которому привязан шаблон
        # Объявленные действия (id -> название). Если заданы, переходы могут
        # использовать только их — так ловятся опечатки в шаблоне.
        self.actions: dict[str, str] = dict(actions or {})
        # Справочник пользователей. Если он задан, включается проверка ролей.
        self.directory = directory
        # Кто может запускать процесс по этому шаблону.
        self.create_roles = frozenset(create_roles)
        if directory is not None:
            directory.check_roles(self.create_roles, "create_roles")
        self._states: dict[str, State] = {}
        self._transitions: dict[tuple[str, str], list[Transition]] = {}
        self._handlers: dict[str, Handler] = {}
        # Предикаты, описанные в шаблоне (id -> предикат): из них собраны условия переходов.
        self.predicates: dict[str, Expression] = {}
        self.add_state(initial_state, final=final, roles=roles, title=initial_title)
        self.initial = initial_state

    # --- динамическая конфигурация --------------------------------------------------
    def add_state(self, name: str, *, final: bool = False, roles: Iterable[str] = (), title: str = "") -> State:
        if not name:
            raise ConfigurationError("имя состояния не может быть пустым")
        if name in self._states:
            raise ConfigurationError(f"состояние {name!r} уже существует")
        roles = frozenset(roles)
        if final and roles:
            raise ConfigurationError(f"в конечном состоянии {name!r} действий нет, роли не нужны")
        if self.directory is not None:
            self.directory.check_roles(roles, f"состояние {name!r}")
        state = State(name, final, roles, title)
        self._states[name] = state
        return state

    def add_transition(
        self,
        source: str,
        event: str,
        target: str,
        guard: Guard | None = None,
        *,
        name: str | None = None,
        condition: Expression | None = None,
    ) -> Transition:
        for state in (source, target):
            if state not in self._states:
                raise ConfigurationError(f"неизвестное состояние {state!r}")
        if self._states[source].final:
            raise ConfigurationError(f"из конечного состояния {source!r} переходов быть не может")
        if not event:
            raise ConfigurationError("имя действия не может быть пустым")
        if self.actions and event not in self.actions:
            raise ConfigurationError(f"действие {event!r} не объявлено в actions шаблона")
        transition = Transition(source, event, target, guard, name, condition or Always())
        self._transitions.setdefault((source, event), []).append(transition)
        return transition

    def register_handler(self, event: str, handler: Handler, *, replace: bool = False) -> None:
        """Зарегистрировать логику для события. Один хэндлер на событие."""
        if not callable(handler):
            raise ConfigurationError(f"хэндлер события {event!r} не вызываемый")
        if event in self._handlers and not replace:
            raise ConfigurationError(f"для события {event!r} хэндлер уже зарегистрирован")
        self._handlers[event] = handler

    def on(self, event: str, *, replace: bool = False) -> Callable[[Handler], Handler]:
        """Декоратор-синоним register_handler: @template.on("submit")."""

        def decorator(handler: Handler) -> Handler:
            self.register_handler(event, handler, replace=replace)
            return handler

        return decorator

    # --- чтение --------------------------------------------------------------------------
    @property
    def states(self) -> tuple[State, ...]:
        return tuple(self._states.values())

    def state(self, name: str) -> State:
        return self._states[name]

    def transitions(self, state: str, event: str) -> list[Transition]:
        """Переходы-кандидаты по паре (состояние, действие) — строка таблицы автомата."""
        return list(self._transitions.get((state, event), []))

    def transitions_from(self, state: str) -> tuple[Transition, ...]:
        return tuple(t for (src, _), ts in self._transitions.items() if src == state for t in ts)

    @property
    def all_transitions(self) -> tuple[Transition, ...]:
        return tuple(t for ts in self._transitions.values() for t in ts)


class ProcessInstance:
    """Экземпляр процесса: конечный автомат с очередью сообщений.

    ProcessInstance("initial") создаёт себе собственный шаблон — так удобно собирать
    автомат вручную; методы add_state / add_transition / register_handler
    меняют этот шаблон.
    """

    def __init__(
        self,
        initial_state: str | None = None,
        *,
        template: ProcessTemplate | None = None,
        final: bool = False,
        roles: Iterable[str] = (),
        context: EvaluationContext | None = None,
        directory: Directory | None = None,
        object_id: str | None = None,
    ) -> None:
        if (template is None) == (initial_state is None):
            raise ConfigurationError("нужно задать либо initial_state, либо template")
        if template is None:
            template = ProcessTemplate(initial_state, final=final, roles=roles, directory=directory)
        self.template = template
        self.object_id = object_id
        # Объект, над которым вычисляются условия переходов.
        self.context = context
        self._queue: deque[Message] = deque()
        self._current = template.initial

    # --- конфигурация — делегируется шаблону ---------------------------------------------
    @property
    def directory(self) -> Directory | None:
        return self.template.directory

    def add_state(self, name: str, **kwargs: Any) -> State:
        return self.template.add_state(name, **kwargs)

    def add_transition(self, *args: Any, **kwargs: Any) -> Transition:
        return self.template.add_transition(*args, **kwargs)

    def register_handler(self, event: str, handler: Handler, *, replace: bool = False) -> None:
        self.template.register_handler(event, handler, replace=replace)

    def on(self, event: str, *, replace: bool = False) -> Callable[[Handler], Handler]:
        return self.template.on(event, replace=replace)

    # --- очередь сообщений ----------------------------------------------------------------
    def send(self, event: str, payload: dict[str, Any] | None = None, *, sender: str | None = None) -> Message:
        """Поставить сообщение в очередь (без обработки)."""
        message = Message(event, dict(payload or {}), sender)
        self._queue.append(message)
        return message

    @property
    def pending(self) -> int:
        return len(self._queue)

    def process_next(self) -> ProcessResult | None:
        """Обработать первое сообщение очереди; None, если очередь пуста."""
        if not self._queue:
            return None
        return self._dispatch(self._queue.popleft())

    def process_all(self) -> list[ProcessResult]:
        """Обработать очередь до опустошения (хэндлер может ставить новые сообщения)."""
        results = []
        while self._queue:
            results.append(self._dispatch(self._queue.popleft()))
        return results

    def handle(self, event: str, payload: dict[str, Any] | None = None, *, sender: str | None = None) -> ProcessResult:
        """Отправить и сразу обработать одно сообщение (очередь должна быть пуста)."""
        if self._queue:
            raise RuntimeError("в очереди есть необработанные сообщения; используйте process_all()")
        self.send(event, payload, sender=sender)
        result = self.process_next()
        assert result is not None
        return result

    # --- состояние --------------------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._current

    @property
    def is_final(self) -> bool:
        return self.template.state(self._current).final

    def available_events(self, user: str | None = None) -> tuple[str, ...]:
        """Действия, предусмотренные в текущем состоянии для пользователя
        (условия здесь не проверяются — только при выполнении)."""
        if self.is_final or self._access_denied(user) is not None:
            return ()
        events = {t.event for t in self.template.transitions_from(self._current)} | set(self.template._handlers)
        return tuple(sorted(events))

    # --- обработка ----------------------------------------------------------------------------
    def _dispatch(self, message: Message) -> ProcessResult:
        source = self._current

        def fail(reason: str, status: str = REJECTED) -> ProcessResult:
            return ProcessResult(message, status, source, source, reason)

        if self.template.state(source).final:
            return fail(f"процесс завершён: состояние {source!r} конечное")

        denied = self._access_denied(message.sender)
        if denied is not None:
            return fail(denied)

        candidates = self.template.transitions(source, message.event)
        handler = self.template._handlers.get(message.event)
        if not candidates and handler is None:
            return fail(f"действие {message.event!r} не предусмотрено в состоянии {source!r}")

        transition: Transition | None = None
        if candidates:
            try:
                allowed = [t for t in candidates if t.allows(self, message)]
            except Exception as exc:  # ошибка вычисления условия
                return fail(f"ошибка вычисления условия: {exc}", ERROR)
            if not allowed:
                return fail(self._explain_failed(message.event, candidates))
            if len(allowed) > 1:
                names = ", ".join(t.label() for t in allowed)
                return fail(f"неоднозначный переход: выполнены условия нескольких переходов ({names})", ERROR)
            transition = allowed[0]

        result = None
        if handler is not None:
            try:
                result = handler(self, message)
            except Exception as exc:
                return fail(f"ошибка в хэндлере события {message.event!r}: {exc}", ERROR)

        target = transition.target if transition else source
        self._current = target
        return ProcessResult(message, ACCEPTED, source, target, None, result)

    def _access_denied(self, user_id: str | None) -> str | None:
        """Причина отказа по правам или None (доступ есть или проверка выключена)."""
        if self.directory is None:
            return None
        user: User | None = self.directory.user(user_id)
        if user is None:
            return f"неизвестный пользователь {user_id!r}"
        state = self.template.state(self._current)
        if not user.has_any_role(state.roles):
            need = ", ".join(sorted(state.roles)) or "—"
            return f"требуется роль {need} (у пользователя {user.id!r} её нет)"
        return None

    def _explain_failed(self, event: str, candidates: list[Transition]) -> str:
        """Причина отказа по условию: какие предикаты оказались ложными."""
        reason = f"условие перехода по действию {event!r} не выполнено"
        if self.context is None:
            return reason
        false = sorted({leaf for t in candidates for leaf in t.condition.false_leaves(self.context)})
        return f"{reason}: ложны {', '.join(false)}" if false else reason


def build_service(
    initial: str,
    states: Iterable[str | tuple[str, bool]] = (),
    transitions: Iterable[tuple] = (),
) -> ProcessInstance:
    """Быстро собрать автомат: states — имена или (имя, final);
    transitions — кортежи (source, event, target[, guard])."""
    service = ProcessInstance(initial)
    for item in states:
        name, final = (item, False) if isinstance(item, str) else item
        if name != initial:
            service.add_state(name, final=final)
    for t in transitions:
        service.add_transition(*t)
    return service
