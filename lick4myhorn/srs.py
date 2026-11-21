"""Spaced repetition (FSRS, the forgetting-curve model used by Anki) for licks.

One card per lick. A review is one practice pass through the lick in the keys you played; you rate
how well it went (Again / Hard / Good / Easy) and FSRS picks the next day to see it. Practice is a
once-a-day activity, so there are no minute-level learning steps: the shortest interval is a day.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from fsrs import Card, Rating, Scheduler

from .db import DB, now_iso

RATINGS = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}


def scheduler(settings: dict) -> Scheduler:
    return Scheduler(desired_retention=float(settings.get("desired_retention", 0.9)), learning_steps=(),
                     relearning_steps=(), maximum_interval=365, enable_fuzzing=True)


def _local_day(iso: str | None) -> str | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso).astimezone().date().isoformat()


def _end_of_today() -> datetime:
    now = datetime.now().astimezone()
    return now.replace(hour=23, minute=59, second=59, microsecond=0)


def state_of(row: dict) -> str:
    """new / learning / reviewing / known / lapsed, from a licks+cards row."""
    if not row.get("reps"):
        return "new"
    if row.get("last_rating") == 1:
        return "lapsed"
    st = row.get("stability") or 0
    if st >= 30:
        return "known"
    if st >= 7:
        return "reviewing"
    return "learning"


def retrievability(db: DB, row: dict, settings: dict) -> float | None:
    c = db.one("SELECT fsrs FROM cards WHERE lick_id=?", (row["id"],))
    if not c or not c["fsrs"]:
        return None
    card = Card.from_dict(json.loads(c["fsrs"]))
    return scheduler(settings).get_card_retrievability(card, datetime.now(timezone.utc))


# What "Good" means for a lick, and what a rating does to the set of keys you are working on. Everything is configurable (Settings).
GOOD_DEF = {"clean_passes": 2,   # clean passes in a row in every key of your set (the "times per key" in practice)
            "min_tempo": 0,      # bpm every key of your set must reach (0: no tempo requirement)
            "sessions": 2,       # Good ratings in a row before one more key joins your set
            "starter_keys": 2,   # keys a new lick starts with, in "my order"
            "add_keys": 1,       # keys added each time you earn it
            "stuck_after": 4,    # Hard/Again ratings in a row before the lick is shelved (and cut back to one key)
            "shelve_days": 21}   # how long it is shelved


def good_def(settings: dict) -> dict:
    return {**GOOD_DEF, **(settings.get("good_def") or {})}


def key_rank(db: DB, lick_id: int, settings: dict) -> list[int]:
    """The order keys join a lick's set: the lick's own key, then your "my order" steps, then the cycle of 4ths."""
    from .licks import key_order
    r = db.one("SELECT tonic_pc FROM licks WHERE id=?", (lick_id,))
    return key_order(int(r["tonic_pc"] or 0), "mine", settings.get("my_steps") or (0, 5, 7, -2))


def lick_scope(db: DB, lick_id: int, settings: dict) -> dict:
    """The keys you are working on for a lick (pitch classes), the keys you have had Good (pc -> when, how fast, under what
    definition), and the streaks that move the set along."""
    row = db.one("SELECT scope, known, good_streak, hard_streak, shelved_until FROM cards WHERE lick_id=?", (lick_id,)) or {}
    gd = good_def(settings)
    rank = key_rank(db, lick_id, settings)
    scope = json.loads(row["scope"]) if row.get("scope") else rank[:max(1, int(gd["starter_keys"]))]
    return {"scope": scope, "known": json.loads(row["known"]) if row.get("known") else {},
            "good_streak": row.get("good_streak") or 0, "hard_streak": row.get("hard_streak") or 0,
            "shelved_until": row.get("shelved_until"), "rank": rank}


def review(db: DB, lick_id: int, rating: int, settings: dict, keys_done=(), trouble_keys=(),
           tempo: int | None = None, mode: str = "drill", note: str = "", planned: bool = True) -> dict:
    row = db.one("SELECT * FROM cards WHERE lick_id=?", (lick_id,))
    card = Card.from_dict(json.loads(row["fsrs"])) if row and row["fsrs"] else Card()
    now = datetime.now(timezone.utc)
    card, _log = scheduler(settings).review_card(card, Rating(int(rating)), now)
    r = int(rating)
    gd, sc = good_def(settings), lick_scope(db, lick_id, settings)
    scope, known, gs, hs, expanded, shelved = list(sc["scope"]), dict(sc["known"]), sc["good_streak"], sc["hard_streak"], [], None
    reviewed_scope = list(scope)   # what this rating was about
    today_s = datetime.now().astimezone().date().isoformat()
    if r >= 3:
        gs, hs = gs + 1, 0
        for pc in scope:   # the rating covers the keys in your set: remember that, how fast, and what "Good" meant then
            kt = db.one("SELECT tempo FROM key_tempo WHERE lick_id=? AND pc=?", (lick_id, pc))
            known[str(pc)] = {"on": today_s, "bpm": (kt or {}).get("tempo"), "passes": gd["clean_passes"], "min_tempo": gd["min_tempo"]}
        if gs >= max(1, int(gd["sessions"])) and len(scope) < 12:
            expanded = [pc for pc in sc["rank"] if pc not in scope][:max(1, int(gd["add_keys"]))]
            scope += expanded
            gs = 0
            if card.due > now + timedelta(days=1):   # the new key is new material: see the lick again tomorrow
                card.due = now + timedelta(days=1)
    else:
        gs, hs = 0, hs + 1
        if hs >= max(1, int(gd["stuck_after"])):
            keep = [pc for pc in scope if str(pc) in known][:1] or scope[:1]
            scope, hs = keep, 0
            shelved = (datetime.now().astimezone().date() + timedelta(days=int(gd["shelve_days"]))).isoformat()
            card.due = now + timedelta(days=int(gd["shelve_days"]))
    due = card.due.isoformat(timespec="seconds")
    reps = (row["reps"] if row else 0) + 1
    # a hard/again lick that was not in the day's plan is something you chose to work on: it takes precedence until it is Good
    chosen = 0 if r >= 3 else (1 if not planned else (row["chosen"] if row and row["chosen"] else 0))
    lapses = (row["lapses"] if row else 0) + (1 if r == 1 and row and row["reps"] else 0)
    db.conn.execute(
        "INSERT OR REPLACE INTO cards(lick_id, fsrs, due, reps, lapses, stability, last_review, last_rating, introduced, chosen, "
        "scope, known, good_streak, hard_streak, shelved_until) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (lick_id, json.dumps(card.to_dict()), due, reps, lapses, card.stability, now_iso(), r,
         (row["introduced"] if row and row["introduced"] else now_iso()), chosen,
         json.dumps(scope), json.dumps(known), gs, hs, shelved))
    db.conn.execute(
        "INSERT INTO reviews(lick_id, ts, rating, keys_done, trouble_keys, tempo, mode, note, due_after, stability, planned, scope, good_def) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (lick_id, now_iso(), r, json.dumps(list(keys_done)), json.dumps(list(trouble_keys)), tempo, mode,
         note, due, card.stability, 1 if planned else 0, json.dumps(reviewed_scope), json.dumps(gd)))
    db.conn.commit()
    days = (card.due - now).total_seconds() / 86400
    return {"due": due, "in_days": round(days, 1), "stability": round(card.stability, 1), "reps": reps,
            "scope": scope, "expanded": expanded, "shelved_until": shelved, "good_streak": gs}


def preview_intervals(db: DB, lick_id: int, settings: dict) -> dict:
    """Days until next review for each rating (shown on the buttons)."""
    row = db.one("SELECT fsrs FROM cards WHERE lick_id=?", (lick_id,))
    base = Card.from_dict(json.loads(row["fsrs"])) if row and row["fsrs"] else Card()
    s = Scheduler(desired_retention=float(settings.get("desired_retention", 0.9)), learning_steps=(),
                  relearning_steps=(), maximum_interval=365, enable_fuzzing=False)
    now = datetime.now(timezone.utc)
    out = {}
    for r in (1, 2, 3, 4):
        c, _ = s.review_card(Card.from_dict(base.to_dict()), Rating(r), now)
        out[r] = max(1, round((c.due - now).total_seconds() / 86400))
    return out


def today(db: DB, settings: dict) -> dict:
    """Today's session. An hour of practice holds about 3-4 licks that are still hard, so those are capped (`focus_cap`) and put first:
    licks you picked yourself and rated Hard/Again outside the plan come before the ones the schedule picked, and the rest wait.
    Then the due licks that are going well, then new licks, which are held back while anything is still Hard/Again."""
    rows = db.licks()
    end = _end_of_today()
    today_s = datetime.now().astimezone().date().isoformat()
    introduced_today = sum(1 for r in rows if _local_day(r.get("introduced")) == today_s)
    reviewed_today = {r["lick_id"] for r in db.q("SELECT DISTINCT lick_id FROM reviews WHERE ts >= ?",
                                                 (datetime.now().astimezone().replace(hour=0, minute=0, second=0)
                                                  .astimezone(timezone.utc).isoformat(timespec="seconds"),))}
    chosen = {r["lick_id"] for r in db.q("SELECT lick_id FROM cards WHERE chosen=1")}
    shelf = {r["lick_id"]: r["shelved_until"] for r in db.q("SELECT lick_id, shelved_until FROM cards WHERE shelved_until > ?", (today_s,))}
    hard, ok, new = [], [], []
    for r in rows:
        if r["id"] in shelf:
            continue
        if not r.get("reps"):
            new.append(r)
        elif r.get("last_rating") in (1, 2):
            hard.append(r)
        elif r.get("due") and datetime.fromisoformat(r["due"]) <= end and r["id"] not in reviewed_today:
            ok.append(r)
    mine = sorted((r for r in hard if r["id"] in chosen), key=lambda r: r.get("last_review") or "", reverse=True)
    sched = sorted((r for r in hard if r["id"] not in chosen and r.get("due") and datetime.fromisoformat(r["due"]) <= end),
                   key=lambda r: (r["last_rating"], r["due"]))
    ranked = mine + sched
    cap = max(1, int(settings.get("focus_cap", 4)))
    focus, backlog = ranked[:cap], ranked[cap:]
    ok.sort(key=lambda r: r["due"])
    ok = ok[: int(settings.get("max_reviews", 20))]
    new.sort(key=lambda r: (-r["favorite"], r["number"]))
    held = bool(settings.get("hold_new", True)) and bool(hard)
    new_quota = 0 if held else max(0, int(settings.get("new_per_day", 2)) - introduced_today)
    return {"focus": [r for r in focus if r["id"] not in reviewed_today], "focus_total": len(focus), "focus_ids": [r["id"] for r in focus], "backlog": backlog,
            "due": ok, "new": new[:new_quota], "done_today": sorted(reviewed_today),
            "new_remaining": len(new), "new_held": held and bool(new), "introduced_today": introduced_today,
            "chosen": sorted(chosen),
            "shelved": [dict(r, shelved_until=shelf[r["id"]]) for r in rows if r["id"] in shelf]}


def stats(db: DB, settings: dict) -> dict:
    rows = db.licks()
    states = Counter(state_of(r) for r in rows)
    reviews = db.q("SELECT ts, rating, lick_id FROM reviews ORDER BY ts")
    by_day = Counter(_local_day(r["ts"]) for r in reviews)
    today_d = datetime.now().astimezone().date()
    days = [(today_d - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    streak = 0
    d = today_d if by_day.get(today_d.isoformat()) else today_d - timedelta(days=1)
    while by_day.get(d.isoformat()):
        streak += 1
        d -= timedelta(days=1)
    upcoming = Counter()
    for r in rows:
        if r.get("due"):
            dd = datetime.fromisoformat(r["due"]).astimezone().date()
            delta = max(0, (dd - today_d).days)
            if delta < 14:
                upcoming[delta] += 1
    funcs = Counter(r["function"] for r in rows)
    ratings = Counter(RATINGS[r["rating"]] for r in reviews[-200:])
    trouble = Counter()
    for r in db.q("SELECT trouble_keys FROM reviews ORDER BY ts DESC LIMIT 300"):
        for k in r["trouble_keys"] or []:
            trouble[k] += 1
    return {
        "licks": len(rows), "states": dict(states), "reviews_total": len(reviews), "streak": streak,
        "per_day": [{"day": d, "n": by_day.get(d, 0)} for d in days],
        "upcoming": [{"in_days": i, "n": upcoming.get(i, 0)} for i in range(14)],
        "functions": dict(funcs.most_common()), "recent_ratings": dict(ratings),
        "trouble_keys": dict(trouble.most_common(12)),
    }
