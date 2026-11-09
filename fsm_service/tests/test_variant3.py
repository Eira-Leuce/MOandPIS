"""Вариант 3 — «Управление заказом на поставку стандартной продукции».

Обязательный минимум сценариев (раздел 4 задания); каждый тест проверяет результат
команды, состояние процесса и запись в журнале:
  * запуск и основной маршрут до конечного состояния;
  * возврат на предыдущий этап;
  * отказ пользователю без необходимой роли;
  * отказ при невыполненном условии перехода;
  * наследование и переопределение параметров;
  * проверка по объектам состава с агрегатором all;
  * записи журнала для успехов и отказов; отказ не меняет состояние.
"""

import pytest

from fsm_service import load_template_yaml

from conftest import V3

ACTOR = {
    "registration": "smirnova", "composition": "smirnova", "review_postponed": "smirnova",
    "price": "kozlov", "delivery": "popova", "payment": "volkov",
    "decision": "morozov", "execution_postponed": "morozov", "execution": "lebedev",
}


def step(v3, obj, action, user=None):
    """Действие от пользователя с ролью текущего состояния."""
    return v3.act(user or ACTOR[v3.state(obj)], obj, action)


def accepted(entry, before, after):
    assert entry["result"] == "accepted", entry["reason"]
    assert (entry["state_before"], entry["state_after"]) == (before, after)


def rejected(v3, entry, obj, reason):
    """Отказ: причина, состояние в журнале не изменилось и совпадает с процессом."""
    assert entry["result"] == "rejected"
    assert reason in entry["reason"], entry["reason"]
    assert entry["state_before"] == entry["state_after"] == v3.state(obj)


def walk_to(v3, obj, target):
    """Довести заказ до состояния target по основному маршруту."""
    route = [("complete", "composition"), ("approve", "price"), ("approve", "delivery"),
             ("approve", "payment"), ("approve", "decision"), ("execute", "execution")]
    v3.start("smirnova", obj)
    for action, state in route:
        before = v3.state(obj)
        if before == target:
            return
        accepted(step(v3, obj, action), before, state)


# --- основной маршрут и возврат ----------------------------------------------------

def test_main_route_to_final_state(v3):
    accepted(v3.start("smirnova", "ord-1"), None, "registration")
    route = [("complete", "registration", "composition"), ("approve", "composition", "price"),
             ("approve", "price", "delivery"), ("approve", "delivery", "payment"),
             ("approve", "payment", "decision"), ("execute", "decision", "execution"),
             ("complete", "execution", "done")]
    for action, before, after in route:
        accepted(step(v3, "ord-1", action), before, after)
    assert v3.engine.process("ord-1").is_final
    rejected(v3, v3.act("lebedev", "ord-1", "complete"), "ord-1", "завершён")
    assert [e["result"] for e in v3.log.entries] == ["accepted"] * 8 + ["rejected"]


def test_return_to_previous_stage(v3):
    walk_to(v3, "ord-1", "delivery")
    accepted(step(v3, "ord-1", "correct"), "delivery", "composition")      # коррекция
    accepted(step(v3, "ord-1", "approve"), "composition", "price")
    accepted(step(v3, "ord-1", "approve"), "price", "delivery")


@pytest.mark.parametrize("state, action, back", [
    ("price", "correct", "composition"),
    ("payment", "correct", "composition"),
    ("decision", "return_to_approval", "composition"),
])
def test_other_returns(v3, state, action, back):
    walk_to(v3, "ord-1", state)
    accepted(step(v3, "ord-1", action), state, back)


def test_postpone_and_resume(v3):
    walk_to(v3, "ord-1", "price")
    accepted(step(v3, "ord-1", "postpone"), "price", "review_postponed")
    accepted(step(v3, "ord-1", "resume"), "review_postponed", "composition")
    walk = [("decision", "defer_decision", "execution_postponed"),
            ("execution_postponed", "reconsider", "decision")]
    for _ in ("approve", "approve", "approve", "approve"):
        step(v3, "ord-1", "approve")
    for before, action, after in walk:
        accepted(step(v3, "ord-1", action), before, after)


# --- отказы по роли -----------------------------------------------------------------

def test_no_role_to_start(v3):
    entry = v3.start("kozlov", "ord-1")
    assert entry["result"] == "rejected" and "sales_manager" in entry["reason"]
    assert v3.state("ord-1") is None and entry["state_after"] is None


def test_no_role_for_action(v3):
    walk_to(v3, "ord-1", "price")
    rejected(v3, v3.act("popova", "ord-1", "approve"), "ord-1", "требуется роль economist")
    assert v3.state("ord-1") == "price"


def test_user_with_two_roles(v3):
    walk_to(v3, "ord-1", "payment")
    accepted(v3.act("morozov", "ord-1", "approve"), "payment", "decision")   # как финансист
    accepted(v3.act("morozov", "ord-1", "execute"), "decision", "execution")  # как директор


# --- отказы по условию и ДНФ -----------------------------------------------------------

def test_registration_needs_customer_and_items(v3):
    v3.start("smirnova", "ord-empty")
    rejected(v3, step(v3, "ord-empty", "complete"), "ord-empty", "has_items")  # «И»: состав пуст


def test_payment_disjunct_full_prepayment(v3):
    walk_to(v3, "ord-urgent", "payment")
    accepted(step(v3, "ord-urgent", "approve"), "payment", "decision")  # первая группа


def test_payment_disjunct_terms_and_not_urgent(v3):
    walk_to(v3, "ord-1", "payment")
    accepted(step(v3, "ord-1", "approve"), "payment", "decision")        # вторая группа


def test_payment_negation_urgent_on_credit(v3):
    walk_to(v3, "ord-urgent-credit", "payment")
    rejected(v3, step(v3, "ord-urgent-credit", "approve"), "ord-urgent-credit", "not urgent")


def test_payment_unset_terms(v3):
    walk_to(v3, "ord-no-terms", "payment")
    entry = step(v3, "ord-no-terms", "approve")
    rejected(v3, entry, "ord-no-terms", "has_payment_terms")
    assert "full_prepayment" in entry["reason"]


def test_urgent_order_cannot_be_deferred(v3):
    walk_to(v3, "ord-urgent", "execution")
    rejected(v3, step(v3, "ord-urgent", "defer_execution"), "ord-urgent", "not urgent")
    accepted(step(v3, "ord-urgent", "complete"), "execution", "done")


# --- наследование -----------------------------------------------------------------------

def test_overridden_range_rejects_long_delivery(v3):
    """90 дней: у supply_order максимум 180, у standard_order переопределён до 60."""
    obj = v3.engine.objects.get("ord-long-delivery")
    assert obj.registry.parameters("supply_order")["delivery_days"].max == 180
    assert obj.get_range("delivery_days") == (1, 60)
    walk_to(v3, "ord-long-delivery", "delivery")
    rejected(v3, step(v3, "ord-long-delivery", "approve"), "ord-long-delivery", "delivery_in_range")


def test_inherited_default_of_subclass(v3):
    """Срок не указан — подставлен default подкласса (14), а не родителя (30); он в диапазоне."""
    assert v3.engine.objects.get("ord-default-delivery").values["delivery_days"] == 14
    walk_to(v3, "ord-default-delivery", "delivery")
    accepted(step(v3, "ord-default-delivery", "approve"), "delivery", "payment")


def test_template_bound_to_subclass_only(v3):
    """Шаблон привязан к standard_order; у базового класса шаблона нет."""
    assert v3.engine.template_for("standard_order").id == "supply_order_management"
    entry = v3.start("smirnova", "base-order")
    assert entry["result"] == "rejected" and "нет шаблона" in entry["reason"]


# --- составность ------------------------------------------------------------------------

def test_composition_quantity_all_rejects(v3):
    """У одной позиции 5000 шт. при максимуме 1000 (диапазон класса order_item) — all ложен."""
    walk_to(v3, "ord-bad-quantity", "composition")
    rejected(v3, step(v3, "ord-bad-quantity", "approve"), "ord-bad-quantity", "items_quantity_ok")


def test_composition_price_all_rejects(v3):
    walk_to(v3, "ord-unpriced", "price")
    rejected(v3, step(v3, "ord-unpriced", "approve"), "ord-unpriced", "items_priced")


def test_composition_default_in_member(v3):
    """item-washer без quantity получил default 1 — позиция проходит проверку диапазона."""
    assert v3.engine.objects.get("item-washer").values["quantity"] == 1
    walk_to(v3, "ord-1", "composition")
    accepted(step(v3, "ord-1", "approve"), "composition", "price")


def test_aggregate_chosen_from_configuration(v3):
    preds = v3.engine.template_for("standard_order").predicates
    assert (preds["items_quantity_ok"].path, preds["items_quantity_ok"].aggregate) == ("items.quantity", "all")
    assert (preds["items_priced"].path, preds["items_priced"].aggregate) == ("items.unit_price", "all")


# --- журнал ------------------------------------------------------------------------------

def test_log_has_success_and_rejections(v3):
    v3.start("kozlov", "ord-1")                       # отказ: роль запуска
    v3.start("smirnova", "ord-1")                     # успех
    v3.act("popova", "ord-1", "complete")             # отказ: роль состояния
    v3.act("smirnova", "ord-1", "approve")            # отказ: действие не предусмотрено
    v3.act("smirnova", "ord-1", "complete")           # успех
    results = [(e["command"]["type"], e["user"], e["result"], e["state_after"]) for e in v3.log.entries]
    assert results == [
        ("start", "kozlov", "rejected", None),
        ("start", "smirnova", "accepted", "registration"),
        ("action", "popova", "rejected", "registration"),
        ("action", "smirnova", "rejected", "registration"),
        ("action", "smirnova", "accepted", "composition"),
    ]


def test_every_state_and_transition_reachable(v3):
    """Шаблон соответствует рис. 3: 10 состояний, 19 переходов."""
    template = load_template_yaml(V3 / "supply_order.yaml")
    assert len(template.states) == 10 and len(template.all_transitions) == 19
    assert [s.name for s in template.states if s.final] == ["done"]
