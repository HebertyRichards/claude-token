"""Turn the runs into the comparison: per model, OFF against ON, over the
runs that actually completed the task. A run that spent less because
it did not finish is a failure, not a saving."""
from statistics import median

from common import fmt_k
from state import FINAL

METRICS = ("cost", "tokens", "out", "turns", "seconds")
# What a task of each level is expected to save, in % of cost: the bands the
# tool is aimed at, shown next to what was measured. Keyed by --kind.
TARGETS = {"baixo": (2, 5), "medio": (7, 12), "alto": (15, 23)}


def _plain(kind):
    return kind.lower().replace("é", "e").strip()


# Shown wherever the task is described without a way to verify it.
UNVERIFIED = ("NENHUMA — sem --check ou --expect o relatório não sabe se a tarefa "
              "foi cumprida, e um braço que parou cedo aparece como mais barato")
VERDICTS = {
    "economizou": "ECONOMIZOU",
    "gastou mais": "GASTOU MAIS",
    "inconclusivo": "inconclusivo: as faixas dos dois braços se sobrepõem",
    "n=1": "n=1: uma execução por braço não separa economia de variação",
    "sem dados": "sem execuções aprovadas nos dois braços — nada a comparar",
}


def _value(attempt, metric):
    if metric == "seconds":
        return attempt.get("seconds")
    return (attempt.get("usage") or {}).get(metric)


def _spread(values):
    if not values:
        return {"n": 0}
    return {"n": len(values), "median": median(values),
            "min": min(values), "max": max(values)}


def _arm(runs):
    good = [r["attempts"][-1] for r in runs if r["status"] == "ok" and r["attempts"]]
    arm = {"runs": sum(r["status"] in FINAL for r in runs), "ok": len(good),
           "invalid": sum(r["status"] == "invalid" for r in runs),
           # Tool calls the headless CLI refused: each one is a wasted turn
           # that an interactive user would have approved or never seen.
           "denials": sum((a.get("cli") or {}).get("denials") or 0 for a in good)}
    used = [a.get("usage") or {} for a in good]
    # What actually ran, not what was typed: the id an alias resolved to, the
    # models subagents used, and how much of the work was theirs.
    arm["ran"] = sorted({m for u in used for m in u.get("models") or []})
    arm["sub_models"] = sorted({m for u in used for m in u.get("sub_models") or []})
    arm["sub_turns"] = sum(u.get("sub_turns") or 0 for u in used)
    arm["sub_cost"] = round(sum(u.get("sub_cost") or 0 for u in used), 6)
    arm["commands"] = sum((u.get("audit") or {}).get("commands", 0)
                          + (u.get("audit") or {}).get("sub_commands", 0) for u in used)
    for m in METRICS:
        arm[m] = _spread([v for v in (_value(a, m) for a in good)
                          if isinstance(v, (int, float))])
    return arm


def _pct(off, on):
    if not off.get("n") or not on.get("n") or not off["median"]:
        return None
    return round((on["median"] - off["median"]) / off["median"] * 100, 1)


def _verdict(off, on):
    """Judged on cost — the figure that weighs cache and output the way the
    plan does. The ranges must not overlap for either word to be used."""
    if not off["cost"].get("n") or not on["cost"].get("n"):
        return "sem dados"
    if min(off["cost"]["n"], on["cost"]["n"]) < 2:
        return "n=1"
    if on["cost"]["max"] < off["cost"]["min"]:
        return "economizou"
    if on["cost"]["min"] > off["cost"]["max"]:
        return "gastou mais"
    return "inconclusivo"


def build(state):
    cells, order = {}, []
    for run in state["runs"]:
        key = (run["scenario"], run["model"])
        if key not in cells:
            cells[key] = {"scenario": run["scenario"], "kind": run.get("kind", ""),
                          "model": run["model"], "runs": {"off": [], "on": []}}
            order.append(key)
        cells[key]["runs"][run["arm"]].append(run)

    out = []
    for key in order:
        cell = cells[key]
        runs = cell.pop("runs")
        cell["off"], cell["on"] = _arm(runs["off"]), _arm(runs["on"])
        cell["delta"] = {m + "_pct": _pct(cell["off"][m], cell["on"][m])
                         for m in ("cost", "tokens", "out", "turns")}
        cell["verdict"] = _verdict(cell["off"], cell["on"])
        out.append(cell)

    totals = []
    for model in dict.fromkeys(c["model"] for c in out):
        both = [c for c in out if c["model"] == model
                and c["off"]["cost"].get("n") and c["on"]["cost"].get("n")]
        if not both:
            continue
        total = {"model": model, "scenarios": len(both)}
        for m in ("cost", "tokens"):
            off = sum(c["off"][m]["median"] for c in both)
            on = sum(c["on"][m]["median"] for c in both)
            total[m] = {"off": off, "on": on,
                        "pct": round((on - off) / off * 100, 1) if off else None}
        totals.append(total)

    attempts = [a for r in state["runs"] for a in r["attempts"]]
    warm = state.get("warmups") or []
    warm_cost = sum(w.get("cost") or 0 for w in warm)
    return {
        "cells": out, "totals": totals,
        "counts": {"total": len(state["runs"]),
                   "settled": sum(r["status"] in FINAL for r in state["runs"])},
        # What measuring cost: every attempt, the failed and the killed too.
        "spent": {"attempts": len(attempts), "warmups": len(warm),
                  "warmup_cost": round(warm_cost, 4),
                  "tokens": (sum(_value(a, "tokens") or 0 for a in attempts)
                             + sum(w.get("tokens") or 0 for w in warm)),
                  "cost": round(sum(_value(a, "cost") or 0 for a in attempts)
                                + warm_cost, 4)},
    }


def _signed(pct):
    return "s/d" if pct is None else f"{pct:+.0f}%"


def _explain(cell):
    off, on, d = cell["off"], cell["on"], cell["delta"]
    if cell["verdict"] == "sem dados":
        text = VERDICTS["sem dados"]
    else:
        text = (f"custo {_signed(d['cost_pct'])} (off ${off['cost']['min']:.4f}–"
                f"${off['cost']['max']:.4f} · on ${on['cost']['min']:.4f}–"
                f"${on['cost']['max']:.4f}) · tokens {_signed(d['tokens_pct'])} · "
                f"saída {_signed(d['out_pct'])} → {VERDICTS[cell['verdict']]}")
    if off["runs"] and on["runs"] and off["ok"] * on["runs"] != on["ok"] * off["runs"]:
        text += " · ATENÇÃO: taxa de sucesso diferente entre os braços"
    if off["denials"] or on["denials"]:
        text += (f" · chamadas negadas por falta de permissão: off "
                 f"{off['denials']}, on {on['denials']}")
    bad = off["invalid"] + on["invalid"]
    if bad:
        text += f" · {bad} execução(ões) inválida(s), fora da conta"
    band = TARGETS.get(_plain(cell.get("kind") or ""))
    if band and d["cost_pct"] is not None:
        saved = -d["cost_pct"]
        text += (f"\n    meta para tarefa de nível {cell['kind']}: economizar "
                 f"{band[0]} a {band[1]}% → medido: "
                 + (f"{saved:.0f}% de economia" if saved >= 0
                    else f"{-saved:.0f}% de gasto a mais")
                 + (" (meta atingida)" if saved >= band[0] else " (abaixo da meta)"))
    ran = sorted(set(off["ran"]) | set(on["ran"]))
    if ran and ran != [cell["model"]]:
        text += "\n    rodou como " + ", ".join(ran)
    if off["sub_turns"] or on["sub_turns"]:
        models = ", ".join(sorted(set(off["sub_models"]) | set(on["sub_models"])))
        text += (f"\n    subagentes ({models}): off {off['sub_turns']} resp, "
                 f"${off['sub_cost']:.4f} · on {on['sub_turns']} resp, "
                 f"${on['sub_cost']:.4f} — já incluídos no custo")
    if off["commands"] or on["commands"]:
        text += (f"\n    comandos de shell executados: off {off['commands']} · "
                 f"on {on['commands']}")
    return text


def render(state):
    s = build(state)
    c = s["counts"]
    stopped = f" • parado: {state['stop_reason']}" if state.get("stop_reason") else ""
    lines = ["", f"benchmark {state['id']} • {c['settled']}/{c['total']} execuções "
                 f"concluídas • braço ON = nível {state['config']['level']}{stopped}", "",
             f"{'cenário':<22}{'modelo':<20}{'braço':<6}{'ok':>5}{'resp':>6}"
             f"{'tokens':>9}{'saída':>8}{'custo':>10}{'tempo':>7}"]
    for cell in s["cells"]:
        for arm in ("off", "on"):
            a, first = cell[arm], arm == "off"
            row = (f"{(cell['scenario'] if first else '')[:21]:<22}"
                   f"{(cell['model'] if first else '')[:19]:<20}{arm:<6}"
                   f"{str(a['ok']) + '/' + str(a['runs']):>5}")
            if a["cost"].get("n"):
                row += (f"{a['turns']['median']:>6.0f}{fmt_k(a['tokens']['median']):>9}"
                        f"{fmt_k(a['out']['median']):>8}"
                        f"{'$%.4f' % a['cost']['median']:>10}"
                        f"{'%.0fs' % a['seconds']['median']:>7}")
            lines.append(row)
        lines.append("  → " + _explain(cell))
    if s["totals"]:
        lines.append("")
    for t in s["totals"]:
        lines.append(
            f"total {t['model']} ({t['scenarios']} cenário(s), soma das medianas): "
            f"custo ${t['cost']['off']:.4f} → ${t['cost']['on']:.4f} "
            f"({_signed(t['cost']['pct'])}) · tokens {fmt_k(t['tokens']['off'])} → "
            f"{fmt_k(t['tokens']['on'])} ({_signed(t['tokens']['pct'])})")
    if not state["config"]["scenario"]["check"]:
        lines += ["", "ATENÇÃO — verificação: " + UNVERIFIED + "."]
    sp = s["spent"]
    lines += ["",
              f"o benchmark gastou {fmt_k(sp['tokens'])} tokens, ~${sp['cost']:.2f}, em "
              f"{sp['attempts']} execução(ões) — as que falharam também contam"
              + (f", e ${sp['warmup_cost']:.2f} disso foi para aquecer o cache."
                 if sp.get("warmups") else "."),
              "negativo = o braço ON gastou menos. Só entram execuções que cumpriram "
              "a tarefa; os valores são medianas.",
              "custo é estimativa pela tabela local de preços. A ordem dos braços "
              "alterna a cada par porque o primeiro paga a gravação do cache."]
    return "\n".join(lines)
