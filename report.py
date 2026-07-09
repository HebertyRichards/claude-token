"""On-demand reports: ON vs OFF comparison and the config waste audit."""
import glob
import os

from common import CLAUDE_DIR, HOME, load_json
from usage import collect


def cmd_report(argv):
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 30
    b = collect(days)
    on, off = b["on"], b["off"]

    print(f"\nclaude-token • last {days} days • costs are ESTIMATES\n")
    print(f"{'':22}{'OFF':>15}{'ON':>15}")
    print("-" * 52)
    for label, key, fmt in [
        ("responses", "turns", "{:,}"),
        ("input tokens", "inp", "{:,}"),
        ("output tokens", "out", "{:,}"),
        ("cache read", "cr", "{:,}"),
        ("cache written", "cw", "{:,}"),
        ("cost (US$)", "cost", "{:,.2f}"),
    ]:
        print(f"{label:22}{fmt.format(off[key]):>15}{fmt.format(on[key]):>15}")

    if off["turns"] and on["turns"]:
        avg_off, avg_on = off["out"] / off["turns"], on["out"] / on["turns"]
        c_off, c_on = off["cost"] / off["turns"], on["cost"] / on["turns"]
        print(f"\navg output/response: {avg_off:,.0f} -> {avg_on:,.0f} tok "
              f"({(1 - avg_on / avg_off) * 100:+.1f}% saved)")
        if c_off:
            print(f"avg cost/response:  ${c_off:.4f} -> ${c_on:.4f} "
                  f"({(1 - c_on / c_off) * 100:+.1f}%)")
            print(f"total saved while ON: ~US$ {(c_off - c_on) * on['turns']:,.2f}")
    elif not on["turns"]:
        print("\nNo usage while ON yet. Turn it on, use Claude Code, rerun report.")
    print()


def cmd_tips():
    print("\nToken-wasting config check\n")

    def check_md(path, label):
        if os.path.exists(path):
            size = os.path.getsize(path)
            flag = "[!]" if size > 4000 else "[ok]"
            print(f"{flag} {label}: {size:,} bytes (~{size // 4:,} tokens every session)")

    check_md(os.path.join(CLAUDE_DIR, "CLAUDE.md"), "global CLAUDE.md")
    for p in sorted(glob.glob(os.path.join(HOME, "projetos", "*", "CLAUDE.md"))):
        check_md(p, f"CLAUDE.md ({os.path.basename(os.path.dirname(p))})")

    cfg = load_json(os.path.join(HOME, ".claude.json"), {})
    mcps = dict(cfg.get("mcpServers", {}))
    for proj in cfg.get("projects", {}).values():
        mcps.update(proj.get("mcpServers", {}))
    if mcps:
        print(f"[!] {len(mcps)} MCP server(s) ({', '.join(mcps)}): each injects "
              "tool schemas into context; disable unused ones")
    else:
        print("[ok] no MCP servers bloating the context")

    print("""
Habits that save the most:
  - specific prompts beat vague ones: vagueness inflates OUTPUT (5x pricier)
  - /clear when switching topics; /compact on long but relevant chats
  - back-to-back turns pay 10% for cached input; >5 min idle loses the cache
  - ask for the snippet, not the whole file
""")
