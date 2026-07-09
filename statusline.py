"""Statusline: short lines (fit narrow terminals) — context fill, session
totals, and ONE clear money comparison against the no-ECO baseline."""
import json
import sys

from common import CONTEXT_WINDOW, fmt_k, load_state
from usage import iter_usage, tail_usage


def compare(actual, normal, money=False):
    # Every value is labeled so there is no doubt which number is which:
    # "com ECO $1.80 · sem ECO ~$6.94 · você economizou $5.14 ✔"
    def f(v):
        return f"${v:.2f}" if money else fmt_k(int(v))
    diff = normal - actual
    both = f"com ECO {f(actual)} · sem ECO ~{f(normal)}"
    if diff >= 0:
        return f"{both} · você economizou {f(diff)} ✔"
    return f"{both} · gastou {f(-diff)} a mais ▲"


def cmd_statusline():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        data = {}
    model = (data.get("model") or {}).get("display_name", "?")
    transcript = data.get("transcript_path", "")
    u = tail_usage(transcript) or {}
    ctx = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
           + u.get("cache_creation_input_tokens", 0))
    pct = ctx / CONTEXT_WINDOW * 100
    dot = "🟢" if pct < 60 else "🟡" if pct < 85 else "🔴"
    cost = (data.get("cost") or {}).get("total_cost_usd") or 0.0

    st = load_state()
    ps = st.get("periods", [])
    on = bool(ps) and ps[-1].get("end") is None
    eco = "ECO✅ ON (/token off desliga)" if on else "ECO❌ OFF (/token on liga)"

    # Session totals: responses, tokens read (input+cache) and written (output).
    turns = out_tok = read_tok = 0
    if transcript:
        for _ts, _model, usage in iter_usage(transcript):
            turns += 1
            out_tok += usage.get("output_tokens", 0)
            read_tok += (usage.get("input_tokens", 0)
                         + usage.get("cache_read_input_tokens", 0)
                         + usage.get("cache_creation_input_tokens", 0))

    print(f"{eco} | {model} | ctx {fmt_k(ctx)}/{CONTEXT_WINDOW//1000}k ({pct:.0f}%) {dot}")
    b_read, b_out, b_cost = (st.get("baseline_read"), st.get("baseline_out"),
                             st.get("baseline_cost"))
    if on and turns and b_read and b_out and b_cost:
        # ON: one metric per line, every value explicitly labeled.
        print(f"leitura: {compare(read_tok, turns * b_read)}")
        print(f"saída:   {compare(out_tok, turns * b_out)}")
        print(f"custo:   {compare(cost, turns * b_cost, money=True)}")
    else:
        # OFF (or no baseline yet): plain session totals.
        print(f"sessão: leitura {fmt_k(read_tok)} · saída {fmt_k(out_tok)} · custo ${cost:.2f}")
