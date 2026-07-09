"""On-demand reports: ON vs OFF comparison, cache efficiency and config audit."""
import glob
import os
from datetime import datetime, timedelta, timezone

from common import CLAUDE_DIR, HOME, PROJECTS, fmt_k, load_json, parse_ts, price_for
from usage import collect, iter_usage


def cmd_cache(argv):
    """Prompt-cache efficiency: hit rate, cold turns and the money they cost."""
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 7
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
                _, _, pcr, pcw = price_for(model)
                waste += w * (pcw - pcr) / 1e6  # extra vs a warm cache read

    total_in = inp + cr + cw
    hit = cr / total_in * 100 if total_in else 0
    print(f"\ncache • últimos {days} dias\n")
    print(f"entrada total   {fmt_k(total_in)} tok")
    print(f"  reaproveitada {fmt_k(cr)} (taxa de acerto {hit:.0f}%, custa 10%)")
    print(f"  regravada     {fmt_k(cw)} (custa 125%)")
    print(f"turnos frios    {cold}/{turns} "
          f"(custo extra ~US$ {waste:,.2f} vs cache quente)")
    print("""
Como manter o cache quente:
  - responda/continue em menos de 5 min (TTL do cache)
  - não edite CLAUDE.md/settings no meio da sessão (invalida o prefixo)
  - prefira continuar a sessão a abrir outra para o mesmo assunto
""")


def cmd_report(argv):
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 30
    b = collect(days)
    on, off = b["on"], b["off"]

    print(f"\nclaude-token • últimos {days} dias • custos são ESTIMATIVAS\n")
    print(f"{'':22}{'DESLIGADO':>15}{'LIGADO':>15}")
    print("-" * 52)
    for label, key, fmt in [
        ("respostas", "turns", "{:,}"),
        ("tokens de entrada", "inp", "{:,}"),
        ("tokens de saída", "out", "{:,}"),
        ("cache lido", "cr", "{:,}"),
        ("cache gravado", "cw", "{:,}"),
        ("custo (US$)", "cost", "{:,.2f}"),
    ]:
        print(f"{label:22}{fmt.format(off[key]):>15}{fmt.format(on[key]):>15}")

    if off["turns"] and on["turns"]:
        avg_off, avg_on = off["out"] / off["turns"], on["out"] / on["turns"]
        c_off, c_on = off["cost"] / off["turns"], on["cost"] / on["turns"]
        print(f"\nmédia saída/resposta: {avg_off:,.0f} -> {avg_on:,.0f} tok "
              f"({(1 - avg_on / avg_off) * 100:+.1f}% de economia)")
        if c_off:
            print(f"média custo/resposta: ${c_off:.4f} -> ${c_on:.4f} "
                  f"({(1 - c_on / c_off) * 100:+.1f}%)")
            print(f"total economizado ligado: ~US$ {(c_off - c_on) * on['turns']:,.2f}")
    elif not on["turns"]:
        print("\nAinda sem uso ligado. Ligue, use o Claude Code e rode o report de novo.")
    print()


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
