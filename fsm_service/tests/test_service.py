import pytest

from fsm_service import ERROR, ConfigurationError, ProcessInstance, build_service


@pytest.fixture
def doc():
    """Упрощённый процесс утверждения документа (из Приложения А задания)."""
    return build_service(
        "preparation",
        states=["normcontrol", ("approved", True), ("cancelled", True)],
        transitions=[
            ("preparation", "submit", "normcontrol"),
            ("preparation", "cancel", "cancelled"),
            ("normcontrol", "reject", "preparation"),
            ("normcontrol", "accept", "approved"),
        ],
    )


# --- очередь сообщений --------------------------------------------------------

def test_send_only_enqueues(doc):
    doc.send("submit")
    assert doc.pending == 1
    assert doc.state == "preparation"


def test_messages_processed_in_fifo_order(doc):
    for event in ("submit", "reject", "submit", "accept"):
        doc.send(event)
    results = doc.process_all()
    assert [r.target for r in results] == ["normcontrol", "preparation", "normcontrol", "approved"]
    assert doc.pending == 0
    assert doc.state == "approved"


def test_process_next_on_empty_queue_returns_none(doc):
    assert doc.process_next() is None


def test_handler_can_enqueue_follow_up_messages():
    service = build_service("a", ["b", "c"], [("a", "go", "b"), ("b", "next", "c")])
    service.register_handler("go", lambda s, m: s.send("next"))
    service.send("go")
    results = service.process_all()
    assert [r.message.event for r in results] == ["go", "next"]
    assert service.state == "c"


def test_handle_refuses_when_queue_not_empty(doc):
    doc.send("submit")
    with pytest.raises(RuntimeError):
        doc.handle("cancel")


# --- переходы и хэндлеры ---------------------------------------------------------

def test_transition_changes_state(doc):
    result = doc.handle("submit", {"user": "ivanov"}, sender="ivanov")
    assert result.accepted and result.changed_state
    assert (result.source, result.target) == ("preparation", "normcontrol")
    assert doc.state == "normcontrol" and result.message.sender == "ivanov"


def test_handler_runs_with_message_and_returns_result(doc):
    seen = []

    @doc.on("submit")
    def on_submit(service, message):
        seen.append((service.state, message.payload["sheets"]))
        return "ok"

    result = doc.handle("submit", {"sheets": 12})
    assert seen == [("preparation", 12)]  # хэндлер видит состояние ДО перехода
    assert result.result == "ok"


def test_handler_without_transition_keeps_state():
    """Событие без переходов, но с хэндлером — внутреннее: логика есть, перехода нет."""
    service = ProcessInstance("idle")
    counter = []
    service.register_handler("ping", lambda s, m: counter.append(1))
    result = service.handle("ping")
    assert result.accepted and not result.changed_state
    assert service.state == "idle" and counter == [1]


def test_self_transition_is_allowed():
    service = build_service("s", transitions=[("s", "retry", "s")])
    assert service.handle("retry").accepted


# --- отказы (состояние не меняется) ------------------------------------------

def assert_rejected(service, result, reason_part):
    assert not result.accepted
    assert reason_part in result.reason
    assert result.source == result.target == service.state


def test_unknown_event_rejected(doc):
    result = doc.handle("approve")
    assert_rejected(doc, result, "не предусмотрено")
    assert doc.state == "preparation"


def test_event_not_valid_in_current_state(doc):
    result = doc.handle("accept")  # accept есть только в normcontrol
    assert_rejected(doc, result, "не предусмотрено")


def test_final_state_rejects_everything(doc):
    doc.handle("cancel")
    assert doc.is_final
    result = doc.handle("submit")
    assert_rejected(doc, result, "конечное")
    assert doc.available_events() == ()


def test_handler_exception_is_error_and_keeps_state(doc):
    @doc.on("submit")
    def boom(service, message):
        raise ValueError("нет обозначения")

    result = doc.handle("submit")
    assert_rejected(doc, result, "нет обозначения")
    assert result.status == ERROR
    assert doc.state == "preparation"


def test_rejected_message_does_not_stop_queue(doc):
    doc.send("accept")  # отказ
    doc.send("submit")  # принимается
    first, second = doc.process_all()
    assert not first.accepted and second.accepted
    assert doc.state == "normcontrol"


# --- предикаты (заготовка под условия в ДНФ) ------------------------------------------

def test_guard_selects_transition():
    service = build_service("normcontrol", ["approval", "approved"])
    small = lambda s, m: m.payload["sheets"] <= 2
    service.add_transition("normcontrol", "accept", "approved", small)
    service.add_transition("normcontrol", "accept", "approval", lambda s, m: not small(s, m))
    assert service.handle("accept", {"sheets": 12}).target == "approval"


def test_guard_false_rejects_and_handler_not_called():
    service = build_service("a", ["b"], [("a", "go", "b", lambda s, m: False)])
    called = []
    service.register_handler("go", lambda s, m: called.append(1))
    result = service.handle("go")
    assert_rejected(service, result, "условие")
    assert called == []


def test_two_true_guards_is_ambiguity_error():
    service = build_service("a", ["b", "c"])
    service.add_transition("a", "go", "b", lambda s, m: True, name="t1")
    service.add_transition("a", "go", "c", lambda s, m: True, name="t2")
    result = service.handle("go")
    assert_rejected(service, result, "неоднозначный переход")
    assert result.status == ERROR  # не выбираем первый попавшийся
    assert "t1" in result.reason and "t2" in result.reason


def test_guard_exception_is_rejection():
    service = build_service("a", ["b"], [("a", "go", "b", lambda s, m: m.payload["missing"])])
    assert_rejected(service, service.handle("go"), "ошибка вычисления условия")


# --- динамическая конфигурация ------------------------------------------------------

def test_states_and_transitions_added_at_runtime(doc):
    doc.handle("submit")
    assert doc.handle("escalate").accepted is False
    doc.add_state("approval")
    doc.add_transition("normcontrol", "escalate", "approval")
    assert doc.handle("escalate").target == "approval"


def test_new_handler_for_new_event_at_runtime():
    service = ProcessInstance("idle")
    assert not service.handle("reload").accepted
    service.register_handler("reload", lambda s, m: "reloaded")
    assert service.handle("reload").result == "reloaded"


def test_available_events(doc):
    assert doc.available_events() == ("cancel", "submit")


def test_config_errors():
    service = ProcessInstance("a")
    with pytest.raises(ConfigurationError):
        service.add_state("a")  # дубликат
    with pytest.raises(ConfigurationError):
        service.add_transition("a", "go", "nowhere")
    service.add_state("end", final=True)
    with pytest.raises(ConfigurationError):
        service.add_transition("end", "go", "a")  # из конечного
    service.register_handler("go", lambda s, m: None)
    with pytest.raises(ConfigurationError):
        service.register_handler("go", lambda s, m: None)
    service.register_handler("go", lambda s, m: 1, replace=True)  # явная замена разрешена
