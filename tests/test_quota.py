from datetime import date

import pytest

from bot.quota import QuotaRule, deadline_for, describe_plan, month_end

AUTO = QuotaRule(fixed=None, min_days=14, cap=25)


def batch(day, count):
    return [day] * count


def test_month_end():
    assert [month_end(date(2026, m, 15)) for m in (1, 2, 4, 12)] == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 4, 30), date(2026, 12, 31)]
    assert month_end(date(2028, 2, 1)) == date(2028, 2, 29)


@pytest.mark.parametrize("imported, days, quota", [
    (date(2026, 10, 1), 31, 5),   # 136 / 31 = 4.4
    (date(2027, 2, 1), 28, 5),    # 136 / 28 = 4.9
    (date(2026, 11, 2), 29, 5),   # scanned late: imported on the 2nd of a 30-day month
])
def test_a_batch_is_spread_until_month_end(imported, days, quota):
    plan = AUTO.plan(batch(imported, 136), imported)
    assert (plan.quota, plan.deadline, plan.days_left, plan.capped) == (quota, month_end(imported), days, False)


def test_quota_stays_the_same_during_the_day():
    morning = AUTO.plan(batch(date(2026, 10, 1), 258), date(2026, 10, 1))
    # later that day the basis still holds every shop searched today, so nothing changes
    assert morning.quota == AUTO.plan(batch(date(2026, 10, 1), 258), date(2026, 10, 1)).quota == 9


def test_missed_days_are_caught_up_later():
    on_pace = AUTO.plan(batch(date(2026, 10, 1), 258 - 9 * 10), date(2026, 10, 11))
    behind = AUTO.plan(batch(date(2026, 10, 1), 258 - 9 * 7), date(2026, 10, 11))  # 3 days missed
    assert (on_pace.quota, behind.quota) == (8, 10)


def test_a_batch_imported_late_in_the_month_gets_the_next_month_too():
    assert deadline_for(date(2026, 10, 23), 14) == date(2026, 11, 30)
    assert deadline_for(date(2026, 10, 18), 14) == date(2026, 10, 31)  # exactly 14 days left
    plan = AUTO.plan(batch(date(2026, 10, 23), 258), date(2026, 10, 23))
    assert (plan.quota, plan.deadline, plan.days_left) == (7, date(2026, 11, 30), 39)


def test_leftovers_from_last_month_go_first_without_flooding_the_day():
    queue = batch(date(2026, 9, 1), 6) + batch(date(2026, 10, 1), 136)
    plan = AUTO.plan(queue, date(2026, 10, 1))
    assert (plan.quota, plan.deadline, plan.due, plan.total) == (6, date(2026, 9, 30), 6, 142)
    assert describe_plan(plan, AUTO) == "โควตาวันนี้ 6 ร้าน (6 ร้านต้องทักให้ทันวันที่ 30/09 อีก 1 วัน · คิวเหลือทั้งหมด 142 ร้าน)"
    after = AUTO.plan(batch(date(2026, 10, 1), 136 - 0), date(2026, 10, 2))
    assert after.quota == 5


def test_overdue_queue_is_capped():
    plan = AUTO.plan(batch(date(2026, 10, 1), 100), date(2026, 11, 15))
    assert (plan.days_left, plan.quota, plan.capped) == (1, 25, True)
    assert "ติดเพดาน 25" in describe_plan(plan, AUTO)


def test_empty_queue_still_allows_one():
    assert AUTO.plan([], date(2026, 10, 1)).quota == 1


def test_fixed_quota_ignores_the_calendar():
    plan = QuotaRule(fixed=15).plan(batch(date(2026, 10, 1), 258), date(2026, 12, 15))
    assert (plan.quota, plan.days_to_finish) == (15, 18)


@pytest.mark.parametrize("value, fixed", [("auto", None), ("AUTO", None), (15, 15), ("12", 12)])
def test_from_config(value, fixed):
    rule = QuotaRule.from_config({"daily_quota": value, "min_days_this_month": 10, "max_daily_quota": 20})
    assert (rule.fixed, rule.min_days, rule.cap) == (fixed, 10, 20)
