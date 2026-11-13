"""Made-up account data in the shape the Polymarket US API returns, so the dashboard can be tried (and photographed)
without an account:  python -m pmdash --demo

Everything here is invented by a seeded random generator. It is internally consistent (every trade's cost is
quantity x price +/- fee, every settlement matches the shares bought), so the P&L model runs on it unchanged."""
import datetime as dt
import random

TEAMS = {
    "nfl": ["Harbor Gulls", "Granite Rams", "Prairie Colts", "Canyon Hawks", "Delta Kings", "Summit Bears"],
    "nba": ["Lake Comets", "Iron Pistons", "Mesa Suns", "Bay Clippers", "Ridge Wolves", "Coast Royals"],
    "cfb": ["Northfield", "Easton State", "Westbrook", "Red Valley", "St. Alder", "Pinecrest Tech"],
    "mlb": ["Dock Mariners", "Hill Cardinals", "Field Twins", "Shore Rays"],
    "nhl": ["Frost Blades", "North Flames", "Steel Wings", "Bay Sharks"],
}
KINDS = [("moneyline", .62), ("spreads", .16), ("totals", .14), ("props", .08)]


def _ts(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond:06d}000Z"


def _amt(x):
    return {"value": f"{x:.4f}", "currency": "USD"}


def raw(seed=21, days=170, n_markets=190):
    r = random.Random(seed)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    acts, positions, uid = [], {}, [0]

    def trade(slug, when, intent, qty, px, meta, market, legs):
        fee = round(0.045 * qty * px * (1 - px) + 0.01, 2)
        cost = qty * px + (fee if "BUY" in intent else -fee)
        uid[0] += 1
        acts.append((when, {"type": "ACTIVITY_TYPE_TRADE", "trade": {
            "id": f"demo-{uid[0]}", "marketSlug": slug, "state": "TRADE_STATE_NEW", "createTime": _ts(when), "updateTime": _ts(when),
            "price": _amt(px), "qtyDecimal": f"{qty:.4f}", "cost": _amt(cost), "isAggressor": True,
            "aggressor": {"intent": intent, "marketMetadata": meta},
            "aggressorExecution": {"commissionNotionalCollected": _amt(fee)}, "market": market, "comboLegDetails": legs}}))
        return cost

    def balance(kind, when, amount, text=""):
        acts.append((when, {"type": f"ACTIVITY_TYPE_{kind}", "accountBalanceChange": {
            "status": "ACCOUNT_BALANCE_CHANGE_STATUS_COMPLETED", "amount": {"value": f"{amount:.2f}", "currency": "USD"},
            "createTime": _ts(when), "updateTime": _ts(when), "description": text}}))

    start = now - dt.timedelta(days=days)
    for d, a in ((0, 500), (20, 400), (55, 300), (90, 600), (128, 400), (150, 300)):
        balance("ACCOUNT_DEPOSIT", start + dt.timedelta(days=d, hours=1), a)
    for d, a in ((70, 350), (120, 500), (163, 420)):
        balance("ACCOUNT_WITHDRAWAL", start + dt.timedelta(days=d, hours=3), a)
    for d, a in ((0, 50), (56, 25), (91, 50), (129, 25), (166, 50)):
        balance("REFERRAL_BONUS", start + dt.timedelta(days=d, hours=2), a, "Deposit bonus")

    for i in range(n_markets):
        # more activity toward the present, and a burst in the last week so short periods have something to show
        age = r.choice([r.uniform(0, 7), r.uniform(0, 30), r.uniform(0, days - 2), r.uniform(0, days - 2)])
        opened = now - dt.timedelta(days=age, hours=r.uniform(1, 9))
        is_open = i < 4
        roll = r.random()
        legs, market = [], {}
        if roll < 0.2:                                           # short crypto markets
            mins = r.choice([5, 5, 15]); coin = r.choice(["BTC", "BTC", "ETH"])
            slug = f"{coin.lower()}-updown-{mins}m-{i}"; hold = dt.timedelta(minutes=mins)
            meta = {"title": f"{coin} Up or Down {mins}m", "outcome": r.choice(["Yes", "No"])}
            market = {"question": meta["title"], "category": "crypto", "marketType": ""}
            px = round(r.uniform(.42, .78), 2); edge = .08
        elif roll < 0.36:                                        # parlays
            n = r.choice([2, 2, 2, 3, 3, 5]); lg = r.choice(list(TEAMS)); picks = r.sample(TEAMS[lg], min(n, len(TEAMS[lg])))
            slug = f"crossd-{i:016x}"; hold = dt.timedelta(hours=r.uniform(3, 30)); meta = {"title": "", "outcome": ""}
            legs = [{"title": f"{p} game", "outcome": p, "team": {"league": lg}} for p in picks]
            px = round(max(.04, min(.6, 0.55 ** len(picks) * r.uniform(.8, 1.3))), 2); edge = -.03
        else:
            lg = r.choices(list(TEAMS), [30, 26, 26, 9, 9])[0]; a, b = r.sample(TEAMS[lg], 2)
            kind = r.choices([k for k, _ in KINDS], [w for _, w in KINDS])[0]
            slug = f"aec-{lg}-{i}"; hold = dt.timedelta(hours=r.uniform(.5, 60))
            meta = {"title": f"{a} vs. {b}", "outcome": a if kind in ("moneyline", "spreads") else r.choice(["Over", "Under"]), "team": {"league": lg}}
            market = {"question": meta["title"], "category": "sports", "marketType": kind}
            px = round(r.uniform(.12, .86), 2); edge = {"nba": .05, "nfl": -.03, "cfb": -.01}.get(lg, .02) + (.06 if px > .5 else -.02)
        if is_open:
            opened = now - dt.timedelta(hours=r.uniform(1, 20))
        stake = r.choice([5, 10, 15, 25, 25, 50, 50, 100, 150, 250]) * (2 if "updown" in slug else 1)
        qty = round(stake / px, 2)
        cost = trade(slug, opened, "ORDER_INTENT_BUY_LONG", qty, px, meta, market, legs)
        if is_open:
            mark = min(.97, max(.03, px + r.uniform(-.12, .16)))
            positions[slug] = {"netPositionDecimal": f"{qty:.4f}", "cashValue": _amt(qty * mark), "cost": _amt(cost), "avgPx": _amt(px),
                               "marketMetadata": meta, "comboLegDetails": legs, "expired": False}
            continue
        closed = min(opened + hold, now - dt.timedelta(minutes=5))
        if r.random() < 0.09 and "updown" not in slug:           # sold before it settled
            trade(slug, closed, "ORDER_INTENT_SELL_LONG", qty, round(min(.95, max(.03, px + r.uniform(-.25, .2))), 2), meta, market, legs)
            continue
        won = r.random() < min(.97, max(.02, px + edge))
        acts.append((closed, {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION", "positionResolution": {
            "marketSlug": slug, "updateTime": _ts(closed), "side": "POSITION_RESOLUTION_SIDE_LONG",
            "beforePosition": {"netPositionDecimal": f"{qty:.4f}", "cashValue": _amt(qty if won else 0), "cost": _amt(cost),
                               "marketMetadata": meta, "comboLegDetails": legs},
            "market": market, "comboLegDetails": legs}}))

    acts.sort(key=lambda a: a[0])
    data = {"activities": [a for _, a in acts], "positions": positions, "balances": [{}]}
    from . import pnl                                           # make the reported cash agree with the made-up history
    cash = pnl.build(data)["check"]["cash_computed"]
    data["balances"] = [{"displayedCash": round(cash, 2) - 25, "displayedBonus": 25, "bonusHold": 25, "currentBalance": round(cash, 2)}]
    return data
