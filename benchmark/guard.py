"""Budget guard. The benchmark spends real plan tokens, so before every run it
reads the account's 5-hour and weekly windows and refuses to start a run that
would cross the ceilings — including one that would cross them by its own
consumption, judged by what the latest runs took."""
from datetime import timedelta

from common import fetch_plan_usage, parse_ts

# key in the usage payload, label for the user, `kind` in its `limits` list
WINDOWS = (("five_hour", "5 h", "session"), ("seven_day", "semana", "weekly_all"))
WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
KEEP_STEPS = 5      # how many recent runs the margin looks at
MIN_STEP = 1.0      # the reading comes in whole points: never assume less


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
    def __init__(self, ceilings, max_cost=0.0, saved=None, fetch=None, enabled=True):
        self.ceilings = {k: v for k, v in ceilings.items() if v}
        self.max_cost = max_cost or 0.0
        self.fetch = fetch or fetch_plan_usage
        self.enabled = enabled
        saved = saved or {}
        self.steps = {k: list(v) for k, v in (saved.get("steps") or {}).items()}
        self.reading = saved.get("reading")
        # The previous reading of THIS process only: one taken before a resume
        # has hours of unrelated usage between it and now, not one run.
        self.last = None

    def snapshot(self):
        return {"steps": self.steps, "reading": self.reading}

    def margin(self, key):
        return max([MIN_STEP] + (self.steps.get(key) or []))

    def check(self, spent):
        """(ok, reason). Call once, right before each run."""
        if self.max_cost and spent >= self.max_cost:
            return False, (f"orçamento de US$ {self.max_cost:.2f} atingido "
                           f"(gasto até aqui: US$ {spent:.2f})")
        if not self.enabled:
            return True, ""
        now = read_windows(self.fetch(timeout=10))
        missing = [label for key, label, _ in WINDOWS
                   if key in self.ceilings and key not in now]
        if missing:
            return False, ("não consegui ler o uso do plano (janela de "
                           + ", ".join(missing) + "): sem essa leitura o teto "
                           "não pode ser garantido. Tente de novo; se a conta "
                           "não é de assinatura, use --no-plan-guard com "
                           "--max-cost")
        if self.last:
            for key in self.ceilings:
                prev = self.last.get(key)
                if prev and _same_window(prev["resets_at"], now[key]["resets_at"]):
                    step = now[key]["pct"] - prev["pct"]
                    if step >= 0:
                        self.steps[key] = (self.steps.get(key, [])
                                           + [round(step, 2)])[-KEEP_STEPS:]
        self.last = self.reading = now
        for key, label, _ in WINDOWS:
            if key not in self.ceilings:
                continue
            pct, top, margin = now[key]["pct"], self.ceilings[key], self.margin(key)
            when = fmt_reset(now[key]["resets_at"])
            if pct >= top:
                return False, (f"janela de {label} em {pct:.0f}%, teto de "
                               f"{top:.0f}% — zera {when}")
            if pct + margin > top:
                return False, (f"janela de {label} em {pct:.0f}%: a próxima "
                               f"execução poderia passar do teto de {top:.0f}% "
                               f"(as últimas consumiram até {margin:.0f} "
                               f"ponto(s) cada) — zera {when}")
        return True, ""
