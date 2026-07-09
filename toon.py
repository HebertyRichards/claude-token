"""Paste preprocessor: shrink what the user is about to paste into a prompt.
JSON with a uniform listing becomes TOON (fields declared once, values only);
anything else is treated as log text (dedupe repeated lines, strip ANSI).
The token saving happens BEFORE pasting — hooks can't rewrite pasted content."""
import json
import re
import sys

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PLAIN = re.compile(r"^[^,\n\"]+$")  # cell values that don't need quoting


def cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    if isinstance(v, str):
        s = v.strip()
        return s if s and PLAIN.match(s) and s == v else json.dumps(v)
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def biggest_table(node, name="items"):
    """The largest list of dicts anywhere in the JSON — the listing to compress."""
    best = (0, None, name)
    if isinstance(node, list) and node and all(isinstance(i, dict) for i in node):
        best = (len(node), node, name)
    if isinstance(node, dict):
        for k, v in node.items():
            n, table, label = biggest_table(v, k)
            if n > best[0]:
                best = (n, table, label)
    elif isinstance(node, list):
        for v in node:
            n, table, label = biggest_table(v, name)
            if n > best[0]:
                best = (n, table, label)
    return best


def to_toon(data):
    n, table, name = biggest_table(data)
    if not table:  # no uniform listing: minified JSON still beats pretty-printed
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    keys = []
    for item in table:
        for k in item:
            if k not in keys:
                keys.append(k)
    lines = [f"{name}[{n}]{{{','.join(keys)}}}:"]
    for item in table:
        lines.append(" " + ",".join(cell(item.get(k)) for k in keys))
    return "\n".join(lines)


def compact_log(text):
    """Collapse consecutive duplicate lines into 'line  [xN]' and strip ANSI codes."""
    out, prev, count = [], None, 0
    for line in ANSI.sub("", text).splitlines():
        line = line.rstrip()
        if line == prev:
            count += 1
            continue
        if count > 1:
            out[-1] += f"  [x{count}]"
        prev, count = line, 1
        out.append(line)
    if count > 1:
        out[-1] += f"  [x{count}]"
    # runs of blank lines add nothing
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return text.strip("\n")


def cmd_toon(argv):
    if argv:
        try:
            with open(argv[0], encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except OSError as e:
            print(f"cannot read {argv[0]}: {e}", file=sys.stderr)
            return
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        print("usage: claude-token toon <file>   (or pipe via stdin)\n"
              "       JSON listing -> TOON; log/text -> repeated lines become [xN]",
              file=sys.stderr)
        return

    raw = raw.strip()
    if not raw:
        return
    try:
        result = to_toon(json.loads(raw))
        mode = "TOON"
    except json.JSONDecodeError:
        result = compact_log(raw)
        mode = "compacted log"

    print(result, flush=True)  # flush so stats (stderr) come after the content
    # Stats go to stderr so stdout can be piped straight to the clipboard.
    before, after = len(raw), len(result)
    pct = (1 - after / before) * 100 if before else 0
    # English on purpose: via /token this line reaches the model's context.
    print(f"\n[{mode}] ~{before // 4} -> ~{after // 4} tokens "
          f"({pct:.0f}% smaller) — paste the output above into the prompt.",
          file=sys.stderr)
