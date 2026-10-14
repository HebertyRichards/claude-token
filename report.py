"""On-demand reports: ON vs OFF comparison, cache efficiency and config audit."""
import glob
import os
from datetime import datetime, timedelta, timezone

from common import (CLAUDE_DIR, HOME, PROJECTS, WRITE_1H, cache_writes, fmt_k,
                    load_json, parse_ts, price_for, price_row, price_warnings)
from usage import collect, session_usage


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
    inp = cr = cw = cw_1h = turns = cold = 0
    sub_turns = sub_cw = 0
    waste = 0.0
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        if os.path.getmtime(path) < cutoff.timestamp():
            continue
        main, subs = session_usage(path)
        # A subagent always starts cold — that is its entry fee, not a habit
        # to fix — so it is counted apart from the session's own cold turns.
        for ts, _model, u in subs:
            dt = parse_ts(ts)
            if dt and dt >= cutoff:
                sub_turns += 1
                sub_cw += u.get("cache_creation_input_tokens", 0)
        for ts, model, u in main:
            dt = parse_ts(ts)
            if not dt or dt < cutoff:
                continue
            i, r = u.get("input_tokens", 0), u.get("cache_read_input_tokens", 0)
            w5, w1 = cache_writes(u)
            w = w5 + w1
            turns += 1
            inp += i; cr += r; cw += w; cw_1h += w1
            if w > r:  # cold turn: rebuilt the cache instead of reusing it
                cold += 1
                p = price_for(model, i + r + w)
                if p:  # extra vs reading the same tokens from a warm cache
                    waste += (w5 * p[3] + w1 * p[0] * WRITE_1H - w * p[2]) / 1e6

    total_in = inp + cr + cw
    hit = cr / total_in * 100 if total_in else 0
    # The TTL is whatever the sessions actually got, not a constant.
    ttl = "1 h" if cw_1h * 2 >= cw else "5 min"
    print(f"\ncache • últimos {days} dias\n")
    print(f"entrada total   {fmt_k(total_in)} tok")
    print(f"  reaproveitada {fmt_k(cr)} (taxa de acerto {hit:.0f}%, custa 2,5-10% conforme o modelo)")
    print(f"  regravada     {fmt_k(cw)} ({fmt_k(cw_1h)} com TTL de 1 h, a 200% da "
          f"entrada; {fmt_k(cw - cw_1h)} de 5 min, a 125%)")
    print(f"turnos frios    {cold}/{turns} "
          f"(custo extra ~US$ {waste:,.2f} vs cache quente)")
    if sub_turns:
        print(f"subagentes      {fmt_k(sub_cw)} gravados em {sub_turns} respostas "
              "(todo subagente parte sem cache)")
    show_price_warnings()
    print(f"""
Como manter o cache quente:
  - continue em menos de {ttl} (o TTL que suas sessões estão recebendo)
  - não edite CLAUDE.md/settings no meio da sessão (invalida o prefixo)
  - prefira continuar a sessão a abrir outra para o mesmo assunto

Use a conversa enquanto o cache está quente: a statusline mostra até que horas
(cache🔥 até HH:MM). Depois que esfria, o próximo turno regrava o contexto
inteiro a preço de gravação, o mais caro que existe:
  - contexto grande: /compact ou conversa nova ANTES de continuar — a
    statusline avisa (cache🧊 reler N ≈$X → /compact ou conversa nova)
  - contexto pequeno: só continue, a regravação custa pouco
  - assunto novo: /clear, com cache quente ou frio
""")


def cmd_report(argv):
    """Straight totals from the transcripts. Deliberately NOT a ligado-vs-
    desligado comparison: the optimizer is meant to stay on, so there is no
    matched off-period to compare against, and a per-turn average from an
    unrelated stretch of past work is not a measurement of what it saved."""
    days = arg_days(argv, 30)
    b = collect(days)
    sub = b["sub"]
    t = {k: b["on"][k] + b["off"][k] + sub[k] for k in sub}

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
    if sub["turns"]:
        # Measured on both sides, so it is a fair pair: what a response costs
        # in the main thread vs in a subagent. Not a claim about what the
        # same work would have cost without delegating.
        main_turns = t["turns"] - sub["turns"]
        main_avg = (t["cost"] - sub["cost"]) / main_turns if main_turns else 0
        print(f"\nsubagentes (já incluídos acima): {sub['turns']:,} respostas · "
              f"{sub['inp'] + sub['cr'] + sub['cw']:,} tok lidos · "
              f"US$ {sub['cost']:,.2f}"
              f"\n  por resposta: ${sub['cost'] / sub['turns']:.4f} no subagente"
              f" · ${main_avg:.4f} na conversa principal")
    # Which row priced each model, main thread and subagents alike: the one
    # place to see that a version is billed at its own rate and no other's.
    print("\npor modelo (linha de preço usada: entrada/saída por milhão):")
    for model, m in sorted(b["models"].items(), key=lambda kv: -kv[1]["cost"]):
        p = price_for(model)
        price = (f"{price_row(model)}: {p[0]:g}/{p[1]:g}" if p
                 else "SEM PREÇO — fora do custo")
        who = " + ".join(x for x in (
            f"{m['turns']:,} resp" if m["turns"] else "",
            f"{m['sub_turns']:,} de subagente" if m["sub_turns"] else "") if x)
        print(f"  {model:<28} {who:<28} US$ {m['cost']:>9,.2f}   {price}")
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
  - turnos seguidos pagam 2,5-10% da entrada em cache (5% no Opus 5.5);
    parado além do TTL (1 h ou 5 min — `claude-token cache` diz qual) esfria
  - subagente em modelo menor barateia cada resposta, mas parte sem cache:
    vale para tarefa isolada, não para uma pergunta rápida
  - peça o trecho, não o arquivo inteiro
""")
