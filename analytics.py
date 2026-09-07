"""Pure aggregation over expense rows. No DB, no framework -- easy to test.

ponytail: aggregates the user's full history in Python on every request. Fine to
~100k rows (<100ms); push the filters into SQL and cache if a user ever exceeds that.
"""
import statistics
from collections import defaultdict
from datetime import date, timedelta

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

mkey = lambda d: f"{d.year:04d}-{d.month:02d}"
mlabel = lambda k: f"{MONTHS[int(k[5:]) - 1]} {k[:4]}"
qkey = lambda d: f"{d.year} Q{(d.month - 1) // 3 + 1}"


def wkey(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def month_shift(k, n):
    y, m = int(k[:4]), int(k[5:])
    t = y * 12 + (m - 1) + n
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


# ---------------------------------------------------------------- filtering

def apply_filters(rows, f):
    out = rows
    if f.get("from"):
        d = date.fromisoformat(f["from"])
        out = [r for r in out if r["d"] >= d]
    if f.get("to"):
        d = date.fromisoformat(f["to"])
        out = [r for r in out if r["d"] <= d]
    if f.get("years"):
        ys = {int(y) for y in f["years"]}
        out = [r for r in out if r["d"].year in ys]
    if f.get("months"):
        ms = {int(m) for m in f["months"]}
        out = [r for r in out if r["d"].month in ms]
    if f.get("categories"):
        cs = set(f["categories"])
        out = [r for r in out if r["category"] in cs]
    if f.get("subcategories"):
        ss = set(f["subcategories"])
        out = [r for r in out if (r["subcategory"] or "") in ss]
    if f.get("payments"):
        ps = set(f["payments"])
        out = [r for r in out if r["payment"] in ps]
    if f.get("weekdays"):
        ws = {int(w) for w in f["weekdays"]}
        out = [r for r in out if r["d"].weekday() in ws]
    if f.get("daytype") in ("weekday", "weekend"):
        want = f["daytype"] == "weekend"
        out = [r for r in out if (r["d"].weekday() >= 5) == want]
    return out


# ---------------------------------------------------------------- helpers

def agg(rows, keyfn):
    out = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for r in rows:
        k = keyfn(r)
        if k is None:
            continue
        b = out[k]
        b["amount"] += r["amount"]
        b["count"] += 1
    return out


def ranked(buckets, total, label=lambda k: k):
    items = [{"key": k, "label": label(k), "amount": round(v["amount"], 2),
              "count": v["count"], "avg": round(v["amount"] / v["count"], 2),
              "pct": round(v["amount"] / total * 100, 2) if total else 0.0}
             for k, v in buckets.items()]
    items.sort(key=lambda x: -x["amount"])
    return items


def series(buckets, keys, label=lambda k: k):
    """Ordered, gap-filled series over an explicit key list."""
    return [{"key": k, "label": label(k),
             "amount": round(buckets.get(k, {}).get("amount", 0.0), 2),
             "count": buckets.get(k, {}).get("count", 0)} for k in keys]


def month_span(rows):
    if not rows:
        return []
    lo, hi = mkey(min(r["d"] for r in rows)), mkey(max(r["d"] for r in rows))
    keys, k = [], lo
    while k <= hi and len(keys) < 600:
        keys.append(k)
        k = month_shift(k, 1)
    return keys


def outlier_score(values):
    """(baseline, score_fn) using the median/MAD modified z-score.

    Mean+stdev is unusable here: a lone outlier inflates its own baseline, and with
    n samples the plain z-score can never exceed (n-1)/sqrt(n) -- 2.04 at n=6, so a
    small history could never flag anything. MAD is resistant to the very points
    being detected. Falls back to mean/stdev when MAD collapses to zero.
    """
    if len(values) < 3:
        return None, None
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    if mad > 0:
        return med, lambda x: 0.6745 * (x - med) / mad
    mean, sd = statistics.fmean(values), statistics.pstdev(values)
    if not sd:
        return None, None
    return mean, lambda x: (x - mean) / sd


def money(v):
    return f"₹{v:,.0f}"


# ---------------------------------------------------------------- anomalies

def detect_anomalies(all_rows, rows):
    """Flag outliers against the user's full history, reported only for rows in view."""
    TXN, AGG = 3.5, 3.0
    found = []
    view_days = {r["d"] for r in rows}
    view_months = {mkey(r["d"]) for r in rows}

    # Score each transaction against its OWN category. A global baseline just surfaces
    # whichever category is most expensive (rent every time) and buries a freak
    # restaurant bill. Categories with too little history fall back to the global one.
    by_cat = defaultdict(list)
    for r in all_rows:
        by_cat[r["category"]].append(r["amount"])
    global_base, global_score = outlier_score([r["amount"] for r in all_rows])
    cat_scores = {c: outlier_score(v) for c, v in by_cat.items() if len(v) >= 5}
    for r in rows:
        base, score = cat_scores.get(r["category"], (global_base, global_score))
        scope = r["category"] if r["category"] in cat_scores else "overall"
        if not score:
            continue
        z = score(r["amount"])
        if z >= TXN:
            found.append({"type": "Transaction", "date": r["d"].isoformat(), "z": round(z, 2),
                          "amount": round(r["amount"], 2), "baseline": round(base, 2),
                          "label": f"{r['category']} \u2014 {r['description'] or 'no description'}",
                          "message": f"Single spend of {money(r['amount'])} vs a typical "
                                     f"{scope} spend of {money(base)}."})

    daily_all = agg(all_rows, lambda r: r["d"])
    base, score = outlier_score([v["amount"] for v in daily_all.values()])
    if score:
        for d, v in daily_all.items():
            if d not in view_days:
                continue
            z = score(v["amount"])
            if z >= AGG:
                found.append({"type": "Spike day", "date": d.isoformat(), "z": round(z, 2),
                              "amount": round(v["amount"], 2), "baseline": round(base, 2),
                              "label": d.strftime("%d %b %Y") + f" ({v['count']} txns)",
                              "message": f"Day total {money(v['amount'])} vs a typical day of {money(base)}."})

    monthly_all = agg(all_rows, lambda r: mkey(r["d"]))
    base, score = outlier_score([v["amount"] for v in monthly_all.values()])
    if score:
        for k, v in monthly_all.items():
            if k not in view_months:
                continue
            z = score(v["amount"])
            if abs(z) >= AGG:
                found.append({"type": "Month", "date": k, "z": round(z, 2),
                              "amount": round(v["amount"], 2), "baseline": round(base, 2),
                              "label": mlabel(k),
                              "message": f"Month total {money(v['amount'])} vs a typical month of {money(base)}."})

    per_cat = defaultdict(dict)
    for (cat, k), v in agg(all_rows, lambda r: (r["category"], mkey(r["d"]))).items():
        per_cat[cat][k] = v["amount"]
    for cat, months in per_cat.items():
        base, score = outlier_score(list(months.values()))
        if not score:
            continue
        for k, amt in months.items():
            if k not in view_months:
                continue
            z = score(amt)
            if z >= AGG:
                found.append({"type": "Category spike", "date": k, "z": round(z, 2),
                              "amount": round(amt, 2), "baseline": round(base, 2),
                              "label": f"{cat} \u2014 {mlabel(k)}",
                              "message": f"{cat} hit {money(amt)} vs its usual {money(base)}."})

    # Cap per type: without this the loudest kind (usually spike days) fills the whole
    # panel and hides the others entirely.
    found.sort(key=lambda a: -abs(a["z"]))
    per_type, out = defaultdict(int), []
    for a in found:
        if per_type[a["type"]] < 8:
            per_type[a["type"]] += 1
            out.append(a)
    return out[:25]


# ---------------------------------------------------------------- limits

def limit_status(all_rows, limits, today):
    out = []
    tkey = mkey(today)
    today_total = sum(r["amount"] for r in all_rows if r["d"] == today)
    month_total = sum(r["amount"] for r in all_rows if mkey(r["d"]) == tkey)
    cat_month = defaultdict(float)
    for r in all_rows:
        if mkey(r["d"]) == tkey:
            cat_month[r["category"]] += r["amount"]

    for lim in limits:
        cap = float(lim["amount"])
        if lim["kind"] == "daily":
            spent, name = today_total, "Daily limit"
        elif lim["kind"] == "monthly":
            spent, name = month_total, "Monthly limit"
        else:
            spent, name = cat_month.get(lim["category"], 0.0), f"{lim['category']} (this month)"
        pct = spent / cap * 100 if cap else 0
        status = "Exceeded" if pct >= 100 else "Approaching" if pct >= 80 else "Within Limit"
        out.append({"id": lim["id"], "kind": lim["kind"], "category": lim.get("category"),
                    "name": name, "cap": round(cap, 2), "spent": round(spent, 2),
                    "pct": round(pct, 1), "status": status})
    return out


# ---------------------------------------------------------------- quality

def data_quality(all_rows):
    issues = []
    seen = defaultdict(list)
    for r in all_rows:
        seen[(r["d"], round(r["amount"], 2), r["category"], (r["description"] or "").strip().lower())].append(r)
    for key, group in seen.items():
        if len(group) > 1:
            issues.append({"kind": "Duplicate", "ids": [r["id"] for r in group],
                           "detail": f"{len(group)}× {money(key[1])} on {key[0].isoformat()} in {key[2]}"})
    for r in all_rows:
        missing = [n for n, v in (("description", r["description"]), ("sub-category", r["subcategory"])) if not v]
        if missing:
            issues.append({"kind": "Missing field", "ids": [r["id"]],
                           "detail": f"{r['d'].isoformat()} {money(r['amount'])} — no {', '.join(missing)}"})
    return issues[:200]


# ---------------------------------------------------------------- insights

def build_insights(k, cats, pays, mom, rows, daily):
    out = []
    if not rows:
        return ["No transactions match the current filters."]
    out.append(f"{k['count']} transactions totalling {money(k['total'])}, "
               f"averaging {money(k['avg_txn'])} per transaction and {money(k['avg_daily'])} per active day.")
    if cats:
        top = cats[0]
        out.append(f"{top['label']} is the biggest driver at {money(top['amount'])} "
                   f"({top['pct']:.1f}% of spend across {top['count']} transactions).")
        if len(cats) > 2:
            top3 = sum(c["pct"] for c in cats[:3])
            out.append(f"The top 3 categories account for {top3:.1f}% of total spend.")
    if mom and mom["prev"] > 0:
        direction = "up" if mom["change"] > 0 else "down"
        out.append(f"{mlabel(mom['current_key'])} is {direction} {money(abs(mom['change']))} "
                   f"({abs(mom['pct']):.1f}%) versus {mlabel(mom['prev_key'])}.")
    if pays:
        p = pays[0]
        out.append(f"{p['label']} is the dominant payment method at {p['pct']:.1f}% of spend "
                   f"(avg {money(p['avg'])} per transaction).")
    if k["highest_day"]:
        out.append(f"Heaviest day in view was {k['highest_day']['label']} at {money(k['highest_day']['amount'])}.")
    if len(daily) >= 4:
        half = len(daily) // 2
        first = sum(d["amount"] for d in daily[:half]) / max(half, 1)
        second = sum(d["amount"] for d in daily[half:]) / max(len(daily) - half, 1)
        if first > 0:
            delta = (second - first) / first * 100
            if abs(delta) >= 10:
                out.append(f"Daily spend is trending {'higher' if delta > 0 else 'lower'} "
                           f"({abs(delta):.0f}%) in the second half of this period.")
    return out


# ---------------------------------------------------------------- main

def compute(all_rows, filters, limits, today=None):
    today = today or date.today()
    rows = apply_filters(all_rows, filters)
    total = sum(r["amount"] for r in rows)

    daily_b = agg(rows, lambda r: r["d"])
    monthly_b = agg(rows, lambda r: mkey(r["d"]))
    cat_b = agg(rows, lambda r: r["category"])
    sub_b = agg(rows, lambda r: (r["category"], r["subcategory"] or "Unspecified"))
    pay_b = agg(rows, lambda r: r["payment"])
    wd_b = agg(rows, lambda r: r["d"].weekday())
    week_b = agg(rows, lambda r: wkey(r["d"]))
    q_b = agg(rows, lambda r: qkey(r["d"]))
    year_b = agg(rows, lambda r: str(r["d"].year))

    cats = ranked(cat_b, total)
    pays = ranked(pay_b, total)
    subs = [{**s, "category": s["key"][0], "label": s["key"][1], "key": " / ".join(s["key"])}
            for s in ranked(sub_b, total)]
    days_ranked = ranked(daily_b, total, label=lambda d: d.strftime("%a, %d %b %Y"))
    for d in days_ranked:
        d["key"] = d["key"].isoformat()
    months_ranked = ranked(monthly_b, total, label=mlabel)

    mkeys = month_span(rows)
    monthly = series(monthly_b, mkeys, mlabel)
    daily = sorted(({"key": d.isoformat(), "label": d.strftime("%d %b %Y"),
                     "amount": round(v["amount"], 2), "count": v["count"]}
                    for d, v in daily_b.items()), key=lambda x: x["key"])
    weekly = sorted(({"key": k, "label": k, "amount": round(v["amount"], 2), "count": v["count"]}
                     for k, v in week_b.items()), key=lambda x: x["key"])

    weekday = [{"key": DAYS[i], "label": DAYS[i],
                "amount": round(wd_b.get(i, {}).get("amount", 0.0), 2),
                "count": wd_b.get(i, {}).get("count", 0),
                "avg": round(wd_b[i]["amount"] / wd_b[i]["count"], 2) if i in wd_b else 0.0}
               for i in range(7)]
    we = sum(r["amount"] for r in rows if r["d"].weekday() >= 5)
    we_days = len({r["d"] for r in rows if r["d"].weekday() >= 5})
    wk_days = len({r["d"] for r in rows if r["d"].weekday() < 5})
    daytype = [{"label": "Weekday", "amount": round(total - we, 2),
                "avg": round((total - we) / wk_days, 2) if wk_days else 0.0},
               {"label": "Weekend", "amount": round(we, 2),
                "avg": round(we / we_days, 2) if we_days else 0.0}]

    active_days = len(daily_b) or 1
    tkey = mkey(today)
    kpis = {
        "total": round(total, 2),
        "count": len(rows),
        "avg_txn": round(total / len(rows), 2) if rows else 0.0,
        "avg_daily": round(total / active_days, 2),
        "avg_monthly": round(total / max(len(monthly_b), 1), 2),
        "active_days": len(daily_b),
        "month_total": round(sum(r["amount"] for r in all_rows if mkey(r["d"]) == tkey), 2),
        "today_total": round(sum(r["amount"] for r in all_rows if r["d"] == today), 2),
        "highest_day": days_ranked[0] if days_ranked else None,
        "lowest_day": days_ranked[-1] if days_ranked else None,
        "highest_category": cats[0] if cats else None,
        "lowest_category": cats[-1] if cats else None,
        "highest_month": months_ranked[0] if months_ranked else None,
        "lowest_month": months_ranked[-1] if months_ranked else None,
    }

    mom = None
    if mkeys:
        cur = mkeys[-1]
        prev = month_shift(cur, -1)
        c = monthly_b.get(cur, {}).get("amount", 0.0)
        p = monthly_b.get(prev, {}).get("amount", 0.0)
        mom = {"current_key": cur, "prev_key": prev, "current": round(c, 2), "prev": round(p, 2),
               "change": round(c - p, 2), "pct": round((c - p) / p * 100, 2) if p else 0.0}

    # Month-over-month deltas for the trend page.
    mom_series = []
    for i, m in enumerate(monthly):
        prev = monthly[i - 1]["amount"] if i else None
        mom_series.append({**m, "change": round(m["amount"] - prev, 2) if prev is not None else None,
                           "pct": round((m["amount"] - prev) / prev * 100, 2) if prev else None})

    # Top-5 category trend + "what moved this month" narratives (needs category x month).
    top5 = [c["key"] for c in cats[:5]]
    cm = agg(rows, lambda r: (r["category"], mkey(r["d"])) if r["category"] in top5 else None)
    cat_trend = {
        "labels": [mlabel(k) for k in mkeys],
        "series": [{"name": c, "data": [round(cm.get((c, k), {}).get("amount", 0.0), 2) for k in mkeys]}
                   for c in top5],
    }

    all_cm = agg(rows, lambda r: (r["category"], mkey(r["d"])))
    narrative = []
    for i in range(1, len(mkeys)):
        cur, prev = mkeys[i], mkeys[i - 1]
        delta = monthly[i]["amount"] - monthly[i - 1]["amount"]
        if not delta:
            continue
        moves = sorted(
            ((c, all_cm.get((c, cur), {}).get("amount", 0.0) - all_cm.get((c, prev), {}).get("amount", 0.0))
             for c in {k[0] for k in all_cm}), key=lambda x: -abs(x[1]))[:2]
        drivers = ", ".join(f"{c} {'+' if v > 0 else '-'}{money(abs(v))}" for c, v in moves if v)
        narrative.append({
            "month": mlabel(cur), "change": round(delta, 2),
            "pct": round(delta / monthly[i - 1]["amount"] * 100, 1) if monthly[i - 1]["amount"] else None,
            "text": f"{mlabel(cur)} came in {money(abs(delta))} "
                    f"{'higher' if delta > 0 else 'lower'} than {mlabel(prev)}"
                    + (f", driven by {drivers}." if drivers else "."),
        })
    narrative.reverse()

    at_rows = all_rows
    at_monthly_b = agg(at_rows, lambda r: mkey(r["d"]))
    at_keys = month_span(at_rows)
    at_monthly = series(at_monthly_b, at_keys, mlabel)
    run = 0.0
    cumulative = []
    for m in at_monthly:
        run += m["amount"]
        cumulative.append({**m, "cumulative": round(run, 2)})
    at_daily = agg(at_rows, lambda r: r["d"])
    all_time = {
        "first_date": min((r["d"] for r in at_rows), default=None),
        "last_date": max((r["d"] for r in at_rows), default=None),
        "total": round(sum(r["amount"] for r in at_rows), 2),
        "count": len(at_rows),
        "months_tracked": len(at_monthly_b),
        "cumulative": cumulative,
        "yearly": ranked(agg(at_rows, lambda r: str(r["d"].year)), sum(r["amount"] for r in at_rows)),
        "record_day": max(({"label": d.strftime("%d %b %Y"), "amount": round(v["amount"], 2)}
                           for d, v in at_daily.items()), key=lambda x: x["amount"], default=None),
        "record_month": max(({"label": mlabel(k), "amount": round(v["amount"], 2)}
                             for k, v in at_monthly_b.items()), key=lambda x: x["amount"], default=None),
        "record_txn": max(({"label": f"{r['category']} — {r['description'] or 'no description'}",
                            "date": r["d"].isoformat(), "amount": round(r["amount"], 2)} for r in at_rows),
                          key=lambda x: x["amount"], default=None),
    }
    for key in ("first_date", "last_date"):
        if all_time[key]:
            all_time[key] = all_time[key].isoformat()
    if all_time["first_date"]:
        span = (date.fromisoformat(all_time["last_date"]) - date.fromisoformat(all_time["first_date"])).days + 1
        all_time["days_tracked"] = span
        all_time["avg_per_day"] = round(all_time["total"] / span, 2)
    else:
        all_time["days_tracked"] = 0
        all_time["avg_per_day"] = 0.0

    return {
        "kpis": kpis,
        "mom": mom,
        "categories": cats,
        "subcategories": subs,
        "payments": pays,
        "monthly": monthly,
        "mom_series": mom_series,
        "cat_trend": cat_trend,
        "narrative": narrative,
        "daily": daily,
        "weekly": weekly,
        "weekday": weekday,
        "daytype": daytype,
        "quarterly": ranked(q_b, total),
        "yearly": ranked(year_b, total),
        "top_days": days_ranked[:10],
        "top_txns": [{"id": r["id"], "date": r["d"].isoformat(), "amount": round(r["amount"], 2),
                      "category": r["category"], "subcategory": r["subcategory"],
                      "description": r["description"], "payment": r["payment"]}
                     for r in sorted(rows, key=lambda r: -r["amount"])[:10]],
        "anomalies": detect_anomalies(all_rows, rows),
        "limits": limit_status(all_rows, limits, today),
        "quality": data_quality(all_rows),
        "insights": build_insights(kpis, cats, pays, mom, rows, daily),
        "all_time": all_time,
    }
