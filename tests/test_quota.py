from datetime import date

import pytest

from bot.quota import QuotaRule, describe_plan

IMPORTED = date(2026, 11, 1)
AUTO = QuotaRule(fixed=None, finish_days=30, cap=25)


def test_a_new_batch_is_spread_over_30_days():
    plan = AUTO.plan(remaining=258, searched_today=0, oldest_import=IMPORTED, today=IMPORTED)
    assert (plan.quota, plan.days_left, plan.capped) == (9, 30, False)  # ceil(258 / 30)


def test_quota_stays_the_same_during_the_day():
    morning = AUTO.plan(258, 0, IMPORTED, IMPORTED)
    after_five = AUTO.plan(253, 5, IMPORTED, IMPORTED)
    assert morning.quota == after_five.quota == 9


def test_missed_days_are_caught_up_later():
    on_pace = AUTO.plan(258 - 9 * 10, 0, IMPORTED, date(2026, 11, 11))
    behind = AUTO.plan(258 - 9 * 7, 0, IMPORTED, date(2026, 11, 11))  # 3 days missed
    assert on_pace.quota == 9
    assert behind.quota == 10


def test_overdue_queue_is_capped():
    plan = AUTO.plan(100, 0, IMPORTED, date(2026, 12, 15))
    assert (plan.days_left, plan.quota, plan.capped) == (1, 25, True)
    assert "ติดเพดาน 25" in describe_plan(plan, AUTO)


def test_small_or_empty_queue_still_allows_one():
    assert AUTO.plan(1, 0, IMPORTED, IMPORTED).quota == 1
    assert AUTO.plan(0, 0, None, IMPORTED).quota == 1


def test_fixed_quota_ignores_the_calendar():
    plan = QuotaRule(fixed=15).plan(258, 0, IMPORTED, date(2026, 12, 15))
    assert (plan.quota, plan.days_to_finish) == (15, 18)


@pytest.mark.parametrize("value, fixed", [("auto", None), ("AUTO", None), (15, 15), ("12", 12)])
def test_from_config(value, fixed):
    rule = QuotaRule.from_config({"daily_quota": value, "finish_within_days": 28, "max_daily_quota": 20})
    assert (rule.fixed, rule.finish_days, rule.cap) == (fixed, 28, 20)
