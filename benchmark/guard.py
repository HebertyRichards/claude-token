"""Budget guard. The benchmark spends real plan tokens, so before every run it
reads the account's 5-hour and weekly windows and works out how many dollars
that run may cost without crossing a ceiling. The amount goes to the CLI as
the run's hard budget: a run that would cross the line is cut by the CLI
itself instead of being discovered afterwards."""
import os
from datetime import timedelta

from common import fetch_plan_usage, load_json, parse_ts, save_json

# key in the usage payload, label for the user, `kind` in its `limits` list
WINDOWS = (("five_hour", "5 h", "session"), ("seven_day", "semana", "weekly_all"))
WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
# Plan points that one dollar of usage costs, per window, until this account's
# own rate has been measured. Deliberately the rate of the smallest plan: on a
# bigger one a dollar moves the window less, so the default only ever errs
# towards stopping early.
DEFAULT_RATE = {"five_hour": 7.0, "seven_day": 1.0}
RATE_FILE = "plan-rate.json"    # beside the results: shared by every benchmark
RESOLUTION = 1.0    # readings come in whole points: keep one in hand
SAFETY = 0.85       # share of the computed headroom a run may actually use
MIN_BUDGET = 0.05   # USD: with less than this a run cannot do anything useful
LEARN_FROM = 3.0    # points: a smaller move is mostly rounding


def read_windows(data):
    """{window: {"pct", "resets_at"}} out of the usage payload. A window the
    payload does not carry is simply absent — the caller decides what that
    means."""
    out = {}
    if not isinstance(data, dict):
        return out
    limits = [l for l in data.get("limits") or [] if isinstance(l, dict)]
    for key, _label, kind in WINDOWS:
        block = data.get(key) if isinstance(data.get(key), dict) else {}
        pct, resets = block.get("utilization"), block.get("resets_at")
        if not _number(pct):
            for lim in limits:
                if lim.get("kind") == kind and _number(lim.get("percent")):
                    pct, resets = lim["percent"], lim.get("resets_at")
                    break
        if _number(pct):
            out[key] = {"pct": float(pct), "resets_at": resets}
    return out


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def fmt_reset(iso):
    dt = parse_ts(iso)
    if not dt:
        return "?"
    # The endpoint returns 05:19:59.99 as often as 05:20:00.37: round to the
    # minute instead of printing "02:19" for a window that resets at 02:20.
    local = (dt + timedelta(seconds=30)).astimezone()
    return f"{WEEKDAYS[local.weekday()]} {local:%H:%M}"


def _same_window(a, b):
    ta, tb = parse_ts(a), parse_ts(b)
    return bool(ta and tb) and abs((ta - tb).total_seconds()) < 120


class Guard:
    def __init__(self, ceilings, max_cost=0.0, folder="", fetch=None, enabled=True):
        self.ceilings = {k: v for k, v in ceilings.items() if v}
        self.max_cost = max_cost or 0.0
        self.fetch = fetch or fetch_plan_usage
        self.enabled = enabled
        self.reading = None
        # What a dollar costs in plan points is a property of the account, not
        # of one benchmark, so it is kept beside the results and every run of
        # every benchmark starts from what the earlier ones measured.
        self.rate_path = os.path.join(folder, RATE_FILE) if folder else ""
        self.rates, self.measured = dict(DEFAULT_RATE), set()
        saved = load_json(self.rate_path, {}) if self.rate_path else {}
        for key in DEFAULT_RATE:
            if isinstance(saved, dict) and _number(saved.get(key)) and saved[key] > 0:
                self.rates[key] = float(saved[key])
                self.measured.add(key)

    def snapshot(self):
        return {"reading": self.reading, "rates": self.rates,
                "measured": sorted(self.measured)}

    def check(self, spent, need=MIN_BUDGET):
        """(ok, reason, budget). Call once, right before each run. `need` is
        what a run of this benchmark has been costing; `budget` is the most
        the next one may cost, in USD (None when no plan window limits it)."""
        if self.max_cost and spent >= self.max_cost:
            return False, (f"orçamento de US$ {self.max_cost:.2f} atingido "
                           f"(gasto até aqui: US$ {spent:.2f})"), None
        if not self.enabled:
            return True, "", None
        now = read_windows(self.fetch(timeout=10))
        missing = [label for key, label, _ in WINDOWS
                   if key in self.ceilings and key not in now]
        if missing:
            return False, ("não consegui ler o uso do plano (janela de "
                           + ", ".join(missing) + "): sem essa leitura o teto "
                           "não pode ser garantido. Tente de novo; se a conta "
                           "não é de assinatura, use --no-plan-guard com "
                           "--max-cost"), None
        self.reading = now
        budget = None
        for key, label, _ in WINDOWS:
            if key not in self.ceilings:
                continue
            pct, top = now[key]["pct"], self.ceilings[key]
            when = fmt_reset(now[key]["resets_at"])
            if pct >= top:
                return False, (f"janela de {label} em {pct:.0f}%, teto de "
                               f"{top:.0f}% — zera {when}"), None
            allowed = (top - pct - RESOLUTION) / self.rates[key] * SAFETY
            if allowed < need:
                return False, (
                    f"janela de {label} em {pct:.0f}%: a folga até o teto de "
                    f"{top:.0f}% comporta ~US$ {max(allowed, 0):.2f}, menos do "
                    f"que uma execução vem custando (~US$ {need:.2f}) — zera "
                    f"{when}"), None
            budget = allowed if budget is None else min(budget, allowed)
        return True, "", budget

    def learn(self, before, cost):
        """After a run: read the windows again and, when the run moved one
        visibly, record how many points a dollar costs on this account. The
        highest rate seen is the one kept — the cautious one. Returns the new
        reading."""
        if not self.enabled or not before:
            return None
        after = read_windows(self.fetch(timeout=10))
        learned = False
        for key in self.ceilings:
            a, b = after.get(key), before.get(key)
            if not a or not b or not _same_window(b["resets_at"], a["resets_at"]):
                continue
            step = a["pct"] - b["pct"]
            if step >= LEARN_FROM and cost > 0:
                rate = step / cost
                if key not in self.measured or rate > self.rates[key]:
                    self.rates[key] = round(rate, 3)
                    self.measured.add(key)
                    learned = True
        if learned and self.rate_path:
            try:
                os.makedirs(os.path.dirname(self.rate_path), exist_ok=True)
                save_json(self.rate_path, {k: self.rates[k] for k in sorted(self.measured)})
            except OSError:
                pass
        if after:
            self.reading = after
        return after
