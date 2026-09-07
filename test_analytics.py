"""One runnable check for the aggregation logic: python3 test_analytics.py"""
from datetime import date
import analytics as A

def row(i, d, amt, cat, sub, pay, desc="x"):
    return {"id": i, "d": date.fromisoformat(d), "amount": amt, "category": cat,
            "subcategory": sub, "description": desc, "payment": pay}

ROWS = [
    row(1, "2026-09-01", 100.0, "Food", "Groceries", "UPI"),
    row(2, "2026-09-01", 50.0,  "Transport", "Fuel", "Cash"),
    row(3, "2026-09-05", 200.0, "Food", "Restaurants", "Debit"),   # Saturday
    row(4, "2026-10-02", 300.0, "Food", "Groceries", "UPI"),
    row(5, "2026-10-15", 5000.0, "Shopping", "Electronics", "Debit"),  # outlier
    row(6, "2026-11-03", 150.0, "Transport", "Cab", "UPI"),
]
TODAY = date(2026, 11, 3)

r = A.compute(ROWS, {}, [], today=TODAY)
k = r["kpis"]
assert k["total"] == 5800.0, k["total"]
assert k["count"] == 6
assert k["avg_txn"] == round(5800/6, 2)
assert k["active_days"] == 5
assert k["avg_daily"] == round(5800/5, 2)
assert k["today_total"] == 150.0
assert k["month_total"] == 150.0          # Nov 2026 only
assert k["highest_category"]["key"] == "Shopping"
assert k["lowest_category"]["key"] == "Transport"
assert k["highest_month"]["key"] == "2026-10"

# category percentages sum to 100
assert abs(sum(c["pct"] for c in r["categories"]) - 100) < 0.05

# monthly series is gap-filled and ordered
assert [m["key"] for m in r["monthly"]] == ["2026-09", "2026-10", "2026-11"]
assert r["monthly"][1]["amount"] == 5300.0

# month-over-month: Nov vs Oct
assert r["mom"]["current_key"] == "2026-11" and r["mom"]["prev"] == 5300.0
assert r["mom"]["change"] == -5150.0

# weekday/weekend split (Sep 5 2026 is a Saturday)
assert dict((d["label"], d["amount"]) for d in r["daytype"]) == {"Weekday": 5600.0, "Weekend": 200.0}

# the 5000 electronics purchase is flagged
assert any(a["type"] == "Transaction" and a["amount"] == 5000.0 for a in r["anomalies"]), r["anomalies"]

# filters
f = A.compute(ROWS, {"categories": ["Food"]}, [], today=TODAY)
assert f["kpis"]["total"] == 600.0 and f["kpis"]["count"] == 3
f = A.compute(ROWS, {"from": "2026-10-01", "to": "2026-10-31"}, [], today=TODAY)
assert f["kpis"]["total"] == 5300.0
f = A.compute(ROWS, {"payments": ["UPI"]}, [], today=TODAY)
assert f["kpis"]["total"] == 550.0
f = A.compute(ROWS, {"daytype": "weekend"}, [], today=TODAY)
assert f["kpis"]["total"] == 200.0

# limits
lim = A.limit_status(ROWS, [{"id": 1, "kind": "daily", "amount": 100, "category": None},
                            {"id": 2, "kind": "monthly", "amount": 180, "category": None},
                            {"id": 3, "kind": "category", "amount": 1000, "category": "Transport"}], TODAY)
assert [l["status"] for l in lim] == ["Exceeded", "Approaching", "Within Limit"], lim

# duplicates
dup = A.data_quality(ROWS + [row(7, "2026-09-01", 100.0, "Food", "Groceries", "UPI")])
assert any(d["kind"] == "Duplicate" for d in dup)
assert any(d["kind"] == "Missing field" for d in A.data_quality([row(8, "2026-09-01", 10.0, "Food", None, "UPI", "")]))

# all-time
at = r["all_time"]
assert at["first_date"] == "2026-09-01" and at["last_date"] == "2026-11-03"
assert at["cumulative"][-1]["cumulative"] == 5800.0
assert at["record_month"]["amount"] == 5300.0

# empty dataset must not explode
e = A.compute([], {}, [], today=TODAY)
assert e["kpis"]["total"] == 0.0 and e["insights"]

print("analytics ok")
