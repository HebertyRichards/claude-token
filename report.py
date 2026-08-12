"""On-demand reports: ON vs OFF comparison, cache efficiency and config audit."""
import glob
import os
from datetime import datetime, timedelta, timezone

from common import (CLAUDE_DIR, HOME, PROJECTS, fmt_k, load_json, parse_ts,
                    price_for, price_warnings)
from usage import collect, iter_usage


def arg_days(argv, default):
    """--days N, tolerating a missing or non-numeric value."""
    if "--days" not in argv:
        return default
    i = argv.index("--days") + 1
    try:
        n = int(argv[i])
    except (IndexError, ValueError):
        print(f"--days sem número válido; usando {default}")
        return default
    return n if n > 0 else default


def show_price_warnings():
    for line in price_warnings():
        print(line)


def cmd_cache(argv):
    """Prompt-cache efficiency: hit rate, cold turns and the money they cost."""
    days = arg_days(argv, 7)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    inp = cr = cw = turns = cold = 0
    waste = 0.0
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        if os.path.getmtime(path) < cutoff.timestamp():
            continue
        for ts, model, u in iter_usage(path):
            dt = parse_ts(ts)
            if not dt or dt < cutoff:
                continue
            i, r, w = (u.get("input_tokens", 0),
                       u.get("cache_read_input_tokens", 0),
                       u.get("cache_creation_input_tokens", 0))
            turns += 1
            inp += i; cr += r; cw += w
            if w > r:  # cold turn: rebuilt the cache instead of reusing it
                cold += 1
                p = price_for(model)
                if p:
                    waste += w * (p[3] - p[2]) / 1e6  # extra vs a warm cache read

    total_in = inp + cr + cw
    hit = cr / total_in * 100 if total_in else 0
    print(f"\ncache • últimos {days} dias\n")
    print(f"entrada total   {fmt_k(total_in)} tok")
    print(f"  reaproveitada {fmt_k(cr)} (taxa de acerto {hit:.0f}%, custa 10%)")
    print(f"  regravada     {fmt_k(cw)} (custa 125%)")
    print(f"turnos frios    {cold}/{turns} "
          f"(custo extra ~US$ {waste:,.2f} vs cache quente)")
    show_price_warnings()
    print("""
Como manter o cache quente:
  - responda/continue em menos de 5 min (TTL do cache)
  - não edite CLAUDE.md/settings no meio da sessão (invalida o prefixo)
  - prefira continuar a sessão a abrir outra para o mesmo assunto
""")


def cmd_report(argv):
    """Straight totals from the transcripts. Deliberately NOT a ligado-vs-
    desligado comparison: the optimizer is meant to stay on, so there is no
    matched off-period to compare against, and a per-turn average from an
    unrelated stretch of past work is not a measurement of what it saved."""
    days = arg_days(argv, 30)
    b = collect(days)
    t = {k: b["on"][k] + b["off"][k] for k in b["on"]}

    print(f"\nclaude-token • últimos {days} dias • custos são ESTIMATIVAS\n")
    if not t["turns"]:
        print("Nenhuma resposta no período.\n")
        return
    for label, key, fmt in [
        ("respostas", "turns", "{:,}"),
        ("tokens de entrada", "inp", "{:,}"),
        ("tokens de saída", "out", "{:,}"),
        ("cache lido", "cr", "{:,}"),
        ("cache gravado", "cw", "{:,}"),
        ("custo (US$)", "cost", "{:,.2f}"),
    ]:
        print(f"{label:22}{fmt.format(t[key]):>15}")
    print(f"\nmédia por resposta: {t['out'] / t['turns']:,.0f} tok de saída · "
          f"{(t['inp'] + t['cr'] + t['cw']) / t['turns']:,.0f} tok lidos · "
          f"${t['cost'] / t['turns']:.4f}")
    show_price_warnings()
    print("\nEm plano de assinatura o dólar acima é referência: o limite que"
          "\naperta é a janela do plano, que a statusline mostra ao vivo.\n")


def cmd_tips():
    print("\nChecagem de desperdício de tokens\n")

    def check_md(path, label):
        if os.path.exists(path):
            size = os.path.getsize(path)
            flag = "[!]" if size > 4000 else "[ok]"
            print(f"{flag} {label}: {size:,} bytes (~{size // 4:,} tokens em toda sessão)")

    check_md(os.path.join(CLAUDE_DIR, "CLAUDE.md"), "CLAUDE.md global")
    for p in sorted(glob.glob(os.path.join(HOME, "projetos", "*", "CLAUDE.md"))):
        check_md(p, f"CLAUDE.md ({os.path.basename(os.path.dirname(p))})")

    cfg = load_json(os.path.join(HOME, ".claude.json"), {})
    mcps = dict(cfg.get("mcpServers", {}))
    for proj in cfg.get("projects", {}).values():
        mcps.update(proj.get("mcpServers", {}))
    if mcps:
        print(f"[!] {len(mcps)} servidor(es) MCP ({', '.join(mcps)}): cada um "
              "injeta schemas no contexto; desative os que não usa")
    else:
        print("[ok] nenhum servidor MCP inchando o contexto")

    print("""
Hábitos que mais economizam:
  - prompt específico vence o vago: vago infla a SAÍDA (5x mais cara)
  - /clear ao trocar de assunto; /compact em conversa longa ainda útil
  - turnos seguidos pagam 10% da entrada em cache; >5 min parado esfria
  - peça o trecho, não o arquivo inteiro
""")
