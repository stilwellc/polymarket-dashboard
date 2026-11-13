"""P&L for a Polymarket US account, built from cash flows so it reconciles to the balance.

Checked against a live account (Nov 2026), to the cent:
  * a BUY (long YES or short NO) costs  qty x price_of_your_side + commission  (= trade.cost)
  * a SELL returns                      qty x price_of_your_side - commission  (= trade.cost)
    and trade.realizedPnl = proceeds - cost basis (fees included on both sides)
  * a settlement pays beforePosition.cashValue (qty x $1 for the winning side, 0 otherwise); the position's
    remaining cost basis is beforePosition.cost (fees included)
  * completed deposits - withdrawals + bonuses + transfers + all of the above = displayedCash + displayedBonus
Their own `realized` field on positions leaves fees out, so it is not used.

Net P&L of a market = sells + payouts - buys (all fees inside) + current value of anything still open."""
import collections
import datetime as dt
import re

_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?Z$")
KNOWN_BONUS = {"REFERRAL_BONUS", "TAKER_FEE_REBATE", "LIQUIDITY_PROGRAM"}
DEAD_TRADES = {"TRADE_STATE_BUSTED", "TRADE_STATE_REJECTED"}


def D(x):
    if isinstance(x, dict):
        x = x.get("value")
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def when_utc(iso):
    """API timestamp -> aware datetime. The API sends nanoseconds ("...:05.123456789Z"); keep six digits."""
    m = _TS.match(iso or "")
    if not m:
        raise ValueError(f"unreadable timestamp {iso!r}")
    return dt.datetime.fromisoformat(m.group(1) + (m.group(2) or "")[:7] + "+00:00")


def local_day(iso):
    """An API timestamp (UTC) -> the calendar day in this Mac's timezone. Days, weeks and months are cut in local
    time: cut in UTC, an evening result in the US lands on the next day (and a Sunday-night one in the next week).
    An unreadable timestamp raises: falling back to the UTC date would silently move results between days."""
    return when_utc(iso).astimezone().date().isoformat()


def _mine(t):
    return (t.get("aggressor") if t.get("isAggressor") else t.get("passive")) or {}


def _myex(t):
    return (t.get("aggressorExecution") if t.get("isAggressor") else t.get("passiveExecution")) or {}


LEAGUES = {"nfl": "NFL", "cfb": "College football", "nba": "NBA", "cbb": "College basketball", "mlb": "MLB", "nhl": "NHL",
           "wnba": "WNBA", "wta": "Tennis", "atp": "Tennis", "fwc": "Soccer", "epl": "Soccer", "mls": "Soccer", "ucl": "Soccer",
           "ufc": "UFC", "setkameua": "Table tennis", "crypto": "Crypto", "politics": "Politics", "macro": "Economy", "sports": "Other sports"}
BET_TYPES = {"moneyline": "Winner", "spreads": "Spread", "totals": "Total", "props": "Player prop", "futures": "Futures"}


def _league(meta, market, slug):
    """Readable category. League from the team, else from the slug ("aec-nfl-sea-den-..."), else the API's category."""
    code = ((meta or {}).get("team") or {}).get("league")
    if not code:
        bits = (slug or "").split("-")
        code = bits[1] if len(bits) > 2 and bits[1] in LEAGUES else (market or {}).get("category")
    return LEAGUES.get(code or "", (code or "other").replace("_", " ").title() if code else "Other")


def _label(meta, market, slug, legs=None):
    """-> (title, outcome, category, bet type). A combo (slug "crossd-...") has no title of its own: name it from
    its legs and file it under Parlays."""
    meta = meta or {}; market = market or {}
    if legs:
        generic = {"yes", "no", "over", "under"}                 # a leg whose outcome says nothing alone: name its market
        picks = [(l.get("outcome") if (l.get("outcome") or "").lower() not in generic and l.get("outcome")
                  else f"{l.get('title') or '?'} {l.get('outcome') or ''}".strip()) for l in legs]
        return "Parlay: " + " + ".join(picks), "", "Parlays", f"Parlay, {len(legs)} legs"
    title = meta.get("title") or market.get("question") or slug
    cat = _league(meta, market, slug)
    kind = BET_TYPES.get(market.get("marketType") or "", "")
    if cat == "Crypto":
        low = title.lower()
        kind = "Crypto 5 min" if " 5m" in low else "Crypto 15 min" if " 15m" in low else "Crypto, other"
    return title, (meta.get("outcome") or ""), cat, kind or "Other"


def build(raw):
    acts, positions = raw.get("activities") or [], raw.get("positions") or {}
    bal = (raw.get("balances") or [{}])[0]
    warn = collections.Counter()                 # anything that could make the numbers wrong is counted, then shown
    ledger = collections.defaultdict(float)      # shares held per market, rebuilt from the trades
    mk = collections.defaultdict(lambda: dict(buys=0.0, sells=0.0, payout=0.0, fees=0.0, staked=0.0, trades=0,
                                             first=None, last=None, title=None, outcome="", league="", kind="", side="", qty_bought=0.0, px_qty=0.0,
                                             settled=False, won=None, open_value=0.0, open_cost=0.0, open_qty=0.0))
    funding = dict(deposits=0.0, withdrawals=0.0, bonuses=0.0, transfers=0.0, pending_deposits=0.0)
    flows, recent = [], []

    def touch(m, t):
        m["first"] = t if not m["first"] or t < m["first"] else m["first"]
        m["last"] = t if not m["last"] or t > m["last"] else m["last"]

    for a in acts:
        ty = a.get("type", "").replace("ACTIVITY_TYPE_", "")
        if a.get("accountBalanceChange"):
            c = a["accountBalanceChange"]; st = c.get("status", "").rsplit("_", 1)[-1]; amt = D(c.get("amount"))
            when = c.get("createTime") or c.get("updateTime")
            if st == "PENDING" and ty == "ACCOUNT_DEPOSIT":
                funding["pending_deposits"] += amt
            if st == "REFUNDED":
                warn["a partially refunded deposit or withdrawal is left out entirely (its kept amount is not in the data)"] += 1
            if st != "COMPLETED":
                continue
            if amt < 0:
                warn["a balance change has a negative amount; its sign was assumed from its type"] += 1
            if (c.get("amount") or {}).get("currency", "USD") != "USD":
                warn["a balance change is not in USD and was added as if it were"] += 1
            key = {"ACCOUNT_DEPOSIT": "deposits", "ACCOUNT_ADVANCED_DEPOSIT": "deposits", "ACCOUNT_WITHDRAWAL": "withdrawals",
                   "TRANSFER": "transfers"}.get(ty, "bonuses")
            if key == "bonuses" and ty not in KNOWN_BONUS:
                warn[f"unrecognised credit type {ty} is counted as a bonus (profit)"] += 1
            if key == "transfers":
                warn["a transfer is counted as incoming profit; the data does not say which way it went"] += 1
            funding[key] += amt
            flows.append((when, -amt if key == "withdrawals" else amt, key))
            recent.append(dict(time=when, kind=key.rstrip("s") if key != "bonuses" else "bonus", text=c.get("description") or "", amount=-amt if key == "withdrawals" else amt))
        elif a.get("trade"):
            t = a["trade"]
            if t.get("state") in DEAD_TRADES:                   # a busted or rejected trade moved no money
                warn["a busted or rejected trade was left out"] += 1
                continue
            o = _mine(t); intent = o.get("intent", ""); slug = t.get("marketSlug"); m = mk[slug]
            if "BUY" not in intent and "SELL" not in intent:
                warn["a trade has no buy/sell intent and was left out"] += 1
                continue
            qty = D(t.get("qtyDecimal") or t.get("qty")); px = D(t.get("price")); fee = D(_myex(t).get("commissionNotionalCollected"))
            side_px = px if "LONG" in intent else 1 - px
            expect = qty * side_px + (fee if "BUY" in intent else -fee)
            cash = D(t.get("cost")) if t.get("cost") is not None else expect
            if abs(cash - expect) > 0.011:
                warn["a trade's cost is not quantity x price +/- fee; the API's cost was used"] += 1
            if (t.get("cost") or {}).get("currency", "USD") != "USD":
                warn["a trade is not in USD and was added as if it were"] += 1
            ledger[slug] += qty if "BUY" in intent else -qty
            when = t.get("createTime") or t.get("updateTime")
            title, outcome, league, kind = _label(o.get("marketMetadata"), t.get("market"), slug, t.get("comboLegDetails"))
            m["title"] = m["title"] or title; m["outcome"] = m["outcome"] or outcome
            m["league"] = m["league"] or league; m["kind"] = m["kind"] or kind
            m["side"] = m["side"] or ("Yes" if "LONG" in intent else "No")
            m["fees"] += fee; m["trades"] += 1; touch(m, when)
            if "BUY" in intent:
                m["buys"] += cash; m["staked"] += cash; m["qty_bought"] += qty; m["px_qty"] += cash    # price paid, fee in
            else:
                m["sells"] += cash
            recent.append(dict(time=when, kind="buy" if "BUY" in intent else "sell", text=f"{title} · {outcome or m['side']}",
                               amount=-cash if "BUY" in intent else cash, qty=qty, price=side_px))
        elif not a.get("positionResolution"):
            warn[f"an activity of type {ty or '?'} has no recognised content and was ignored"] += 1
        else:
            r = a["positionResolution"]; slug = r.get("marketSlug"); m = mk[slug]; b = r.get("beforePosition") or {}
            pay, cost = D(b.get("cashValue")), D(b.get("cost")); when = r.get("updateTime")
            title, outcome, league, kind = _label(b.get("marketMetadata"), r.get("market"), slug, r.get("comboLegDetails") or b.get("comboLegDetails"))
            m["title"] = m["title"] or title; m["outcome"] = m["outcome"] or outcome
            m["league"] = m["league"] or league; m["kind"] = m["kind"] or kind
            m["side"] = m["side"] or ("Yes" if "LONG" in r.get("side", "") else "No")
            if abs(ledger[slug] - abs(D(b.get("netPositionDecimal")))) > 0.011:
                warn["a settlement's share count differs from the shares the trades add up to"] += 1
            if m["settled"]:
                warn["a market settled more than once; every payout was added"] += 1
            ledger[slug] = 0.0
            m["payout"] += pay; m["settled"] = True; m["won"] = pay > 0; touch(m, when)
            recent.append(dict(time=when, kind="won" if pay > 0 else "lost", text=f"{title} · {outcome or m['side']}", amount=pay, pnl=pay - cost))

    open_rows = []
    for slug, p in positions.items():
        qty = D(p.get("netPositionDecimal") or p.get("netPosition"))
        if abs(qty) < 1e-9:
            continue
        m = mk[slug]; meta = p.get("marketMetadata") or {}
        m["open_value"], m["open_cost"], m["open_qty"] = D(p.get("cashValue")), D(p.get("cost")), qty
        if abs(ledger.get(slug, 0.0) - abs(qty)) > 0.011:
            warn["an open position's size differs from the shares the trades add up to"] += 1
        if p.get("expired"):
            warn["an open position is marked expired; its value may be stale"] += 1
        t_, o_, l_, k_ = _label(meta, None, slug, p.get("comboLegDetails"))
        m["title"] = t_ if (not m["title"] or m["title"] == slug or m["title"].startswith("crossd-")) else m["title"]
        m["outcome"] = m["outcome"] or o_; m["league"] = m["league"] or l_; m["kind"] = m["kind"] or k_
        open_rows.append(dict(slug=slug, title=m["title"], outcome=m["outcome"], side="Yes" if qty > 0 else "No", qty=abs(qty),
                              avg=D(p.get("avgPx")), cost=m["open_cost"], value=m["open_value"],
                              unrealized=m["open_value"] - m["open_cost"], league=m["league"] or "Other"))

    # the dangerous one: shares bought, never settled, and missing from the open-positions list would otherwise be
    # booked as a finished market that lost its whole stake
    for slug, q in ledger.items():
        if abs(q) > 0.011 and abs(mk[slug]["open_qty"]) < 1e-9:
            warn["a market still holds shares but is not in the open positions; it is shown as finished at a loss"] += 1
    markets = []
    for slug, m in mk.items():
        net = m["sells"] + m["payout"] - m["buys"] + m["open_value"]
        status = "open" if abs(m["open_qty"]) > 1e-9 else ("won" if m["won"] else "lost") if m["settled"] else "closed"
        hold = (when_utc(m["last"]) - when_utc(m["first"])).total_seconds() / 3600 if m["first"] and m["last"] else None
        markets.append(dict(slug=slug, title=m["title"] or slug, outcome=m["outcome"], side=m["side"], league=m["league"] or "Other",
                            kind=m["kind"] or "Other", status=status,
                            staked=m["buys"], returned=m["sells"] + m["payout"], fees=m["fees"], pnl=net, trades=m["trades"],
                            first=m["first"], last=m["last"], roi=(net / m["buys"] if m["buys"] else None),
                            day=(local_day(m["last"]) if m["last"] else None), hold_hours=hold,
                            price=(m["px_qty"] / m["qty_bought"] if m["qty_bought"] else None),      # avg paid per $1 share, fee in
                            open_cost=m["open_cost"], open_value=m["open_value"]))
    markets.sort(key=lambda r: r["last"] or "", reverse=True)

    trading = sum(r["pnl"] for r in markets)
    unreal = sum(r["unrealized"] for r in open_rows)
    realized = trading - unreal
    cash = sum(f[1] for f in flows) + sum(m["sells"] + m["payout"] - m["buys"] for m in mk.values())
    net_funding = funding["deposits"] - funding["withdrawals"]
    done = [r for r in markets if r["status"] != "open"]
    wins = [r for r in done if r["pnl"] > 0]
    staked = sum(r["staked"] for r in markets)

    # P&L through time: each finished market's whole net result (from its cash flows) is booked on the day it
    # closed, so the line sums exactly to the closed-market total. (The API's per-trade realizedPnl is missing on
    # some sales, so it is not used.) Open positions join at "today".
    by_day = {}
    for r in markets:
        if r["status"] != "open" and r["last"]:
            by_day[local_day(r["last"])] = by_day.get(local_day(r["last"]), 0.0) + r["pnl"]
    # Bonuses count as profit: every credit that is not a deposit (referral, promo, transfer) is
    # booked as profit on the day it landed, in every total, chart and period.
    bonus_rows = [(when, amt) for when, amt, key in flows if key in ("bonuses", "transfers")]
    bonus_day = {}
    for when, amt in bonus_rows:
        bonus_day[local_day(when)] = bonus_day.get(local_day(when), 0.0) + amt
        by_day[local_day(when)] = by_day.get(local_day(when), 0.0) + amt
    by_day = collections.OrderedDict(sorted(by_day.items()))
    bonus_total = sum(a for _, a in bonus_rows)
    closed_pnl = sum(r["pnl"] for r in done)
    curve, run = [], 0.0
    for d, v in by_day.items():
        run += v; curve.append(dict(date=d, day=v, cum=run, bonus=bonus_day.get(d, 0.0),
                                    n=sum(1 for r in done if local_day(r["last"]) == d)))
    months = collections.OrderedDict()
    for d, v in by_day.items():
        months[d[:7]] = months.get(d[:7], 0.0) + v
    weeks = collections.OrderedDict()                          # Monday-start weeks, empty weeks kept as zero
    if by_day:
        monday = lambda d: d - dt.timedelta(days=d.weekday())
        w = monday(dt.date.fromisoformat(next(iter(by_day)))); end = monday(dt.date.today())
        while w <= end:
            weeks[w.isoformat()] = 0.0; w += dt.timedelta(days=7)
        for d, v in by_day.items():
            weeks[monday(dt.date.fromisoformat(d)).isoformat()] += v
    today = dt.date.today(); wk0 = (today - dt.timedelta(days=today.weekday())).isoformat()
    def period(start):
        rs = [r for r in done if r["last"] and local_day(r["last"]) >= start]
        bonus = sum(a for when, a in bonus_rows if local_day(when) >= start)
        return dict(pnl=sum(r["pnl"] for r in rs) + bonus, betting=sum(r["pnl"] for r in rs), bonus=bonus,
                    fees=sum(r["fees"] for r in rs), markets=len(rs), staked=sum(r["staked"] for r in rs))
    this_week, this_day = period(wk0), period(today.isoformat())
    cut7 = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)          # a rolling 7 x 24 hours, to the minute
    r7 = [r for r in done if r["last"] and when_utc(r["last"]) >= cut7]
    b7 = sum(a for when, a in bonus_rows if when_utc(when) >= cut7)
    last7 = dict(pnl=sum(r["pnl"] for r in r7) + b7, betting=sum(r["pnl"] for r in r7), bonus=b7, fees=sum(r["fees"] for r in r7),
                 markets=len(r7), staked=sum(r["staked"] for r in r7))
    leagues = collections.defaultdict(lambda: dict(pnl=0.0, staked=0.0, markets=0, wins=0, done=0))
    for r in markets:
        g = leagues[r["league"] or "Other"]; g["pnl"] += r["pnl"]; g["staked"] += r["staked"]; g["markets"] += 1
        if r["status"] != "open":
            g["done"] += 1; g["wins"] += r["pnl"] > 0
    if bonus_rows:
        leagues["Bonuses"].update(pnl=bonus_total, markets=len(bonus_rows))
    recent.sort(key=lambda r: r["time"] or "", reverse=True)

    reported_cash = D(bal.get("displayedCash")) + D(bal.get("displayedBonus"))
    if abs(cash - reported_cash) >= 0.01:
        warn[f"computed cash is off from Polymarket's reported cash by {cash - reported_cash:+.2f}"] += 1
    held = D(bal.get("bonusHold"))
    notes = [f"{k} ({n}x)" if n > 1 else k for k, n in warn.items()]
    return dict(
        warnings=notes, bonus_hold=held,
        asof=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        summary=dict(trading_pnl=trading, realized=realized, unrealized=unreal, bonuses=funding["bonuses"] + funding["transfers"],
                     total_pnl=trading + funding["bonuses"] + funding["transfers"], fees=sum(r["fees"] for r in markets),
                     staked=staked, roi=((trading + bonus_total) / staked if staked else None),
                     # return on deposits: profit over all the money ever paid in, withdrawals ignored
                     roi_deposits=((trading + bonus_total) / funding["deposits"] if funding["deposits"] else None), markets=len(markets), settled=len(done),
                     win_rate=(len(wins) / len(done) if done else None), cash=cash, open_value=sum(r["value"] for r in open_rows),
                     account_value=cash + sum(r["value"] for r in open_rows), deposits=funding["deposits"],
                     withdrawals=funding["withdrawals"], net_deposited=net_funding, pending_deposits=funding["pending_deposits"],
                     closed_pnl=closed_pnl, open_pnl=trading - closed_pnl,
                     first=min((r["first"] for r in markets if r["first"]), default=None)),
        check=dict(cash_computed=cash, cash_reported=reported_cash, diff=cash - reported_cash,
                   closed_pnl=closed_pnl, bonus_total=bonus_total, curve_end=(curve[-1]["cum"] if curve else 0.0),
                   open_pnl=trading - closed_pnl),
        bonus_list=[dict(time=w_, day=local_day(w_), amount=a_) for w_, a_ in bonus_rows], today_local=today.isoformat(),
        deposit_list=[dict(day=local_day(w_), amount=a_) for w_, a_, k_ in flows if k_ == "deposits"],
        last7=last7, week=dict(start=wk0, **this_week), today=dict(date=today.isoformat(), **this_day),
        curve=curve, months=[dict(month=k, pnl=v) for k, v in months.items()],
        weeks=[dict(week=k, pnl=v) for k, v in weeks.items()],
        leagues=sorted((dict(league=k, **v) for k, v in leagues.items()), key=lambda g: g["pnl"], reverse=True),
        open=sorted(open_rows, key=lambda r: -abs(r["value"])), markets=markets, recent=recent[:60])
