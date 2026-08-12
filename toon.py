"""Paste preprocessor: shrink what the user is about to paste into a prompt.
JSON or XML with a uniform listing becomes TOON (fields declared once, values
only); HTML gets DOM-stripped (scripts/styles/decor out, selectors kept);
.docx is unzipped to plain text; anything else textual is treated as log text
(dedupe repeated lines, strip ANSI). Binary input is refused rather than
mangled. The saving has to happen BEFORE pasting: a hook can add context but
cannot rewrite what the user already pasted, so this stays a manual step."""
import html
import io
import json
import re
import sys
import zipfile
from html.parser import HTMLParser

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PLAIN = re.compile(r"^[^,\n\"]+$")  # cell values that don't need quoting
HTMLISH = re.compile(r"<\s*(!doctype|html|head|body|div|nav|main|section)\b",
                     re.I)
# DOM analysis needs selectors and structure — keep those; scripts, styles,
# inline SVG and visual noise are ~90% of the bytes and carry no meaning.
DOM_KEEP_ATTRS = {"id", "class", "href", "src", "name", "type", "role", "alt",
                  "placeholder", "value", "title", "for", "action", "method"}
DOM_SKIP_TAGS = {"script", "style", "svg", "noscript", "template", "canvas",
                 "iframe", "link", "meta"}
DOM_VOID = {"link", "meta", "br", "hr", "img", "input"}
BLOCK_END = re.compile(r"(</(?:div|section|article|ul|ol|li|tr|table|form|"
                       r"main|header|footer|nav|p|h[1-6])>)")


class DomStripper(HTMLParser):
    """keep: what the user's analysis targets — 'script' preserves <script>
    blocks and on* handlers; 'css' preserves <style> blocks and style= attrs.
    Default strips both: structure/selectors only."""

    def __init__(self, keep=frozenset()):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.raw = [], 0, 0
        self.keep = keep
        self.skip_tags = DOM_SKIP_TAGS - (
            {"script"} if "script" in keep else set()) - (
            {"style"} if "css" in keep else set())

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            if tag not in DOM_VOID:
                self.skip += 1
            return
        if self.skip:
            return
        if tag in ("script", "style"):
            self.raw += 1
        kept = []
        for k, v in attrs:
            ok = (k in DOM_KEEP_ATTRS or k.startswith(("data-", "aria-"))
                  or ("css" in self.keep and k == "style")
                  or ("script" in self.keep and k.startswith("on")))
            if ok and v and not v.startswith("data:"):
                kept.append(f'{k}="{v[:200 if self.raw else 80]}"')
        self.out.append("<" + tag + (" " + " ".join(kept) if kept else "") + ">")

    def handle_endtag(self, tag):
        if tag in self.skip_tags:
            if tag not in DOM_VOID and self.skip:
                self.skip -= 1
            return
        if self.skip or tag in DOM_VOID:
            return
        if tag in ("script", "style") and self.raw:
            self.raw -= 1
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.skip:
            return
        if self.raw:
            # kept JS/CSS: collapsing newlines would swallow code after //
            # comments — trim per line instead.
            d = "\n".join(l.strip() for l in data.splitlines() if l.strip())
            if d:
                self.out.append("\n" + d + "\n")
        else:
            d = " ".join(data.split())
            if d:
                self.out.append(d)


def strip_dom(text, keep=frozenset()):
    p = DomStripper(keep)
    try:
        p.feed(text)
        p.close()
    except Exception:  # malformed HTML: keep whatever was parsed so far
        pass
    return BLOCK_END.sub("\\1\n", "".join(p.out)).strip("\n")


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


TAG = re.compile(r"<[^>]+>")
# Office XML: paragraph/row ends and explicit breaks become newlines before
# the rest of the markup is dropped, so the text keeps its line structure.
DOCX_BREAKS = re.compile(r"</w:p>|<w:br\s*/>|</a:p>|</w:tr>")


def sniff(blob):
    """'pdf' | 'docx' | 'pptx' | 'xlsx' | 'zip' | 'binary' | 'text'."""
    if blob.startswith(b"%PDF"):
        return "pdf"
    if blob.startswith(b"PK\x03\x04"):
        try:
            names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
        except (zipfile.BadZipFile, OSError):
            return "binary"
        if "word/document.xml" in names:
            return "docx"
        if any(n.startswith("ppt/slides/slide") for n in names):
            return "pptx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        return "zip"
    # A NUL in the head is the cheap, reliable "not text" signal; without this
    # check a binary file would be "compacted" into mojibake and reported as a
    # tidy percentage saving.
    return "binary" if b"\x00" in blob[:4096] else "text"


def docx_text(blob):
    """Plain text from a .docx — stdlib only (it is a zip of XML)."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    text = TAG.sub("", DOCX_BREAKS.sub("\n", xml))
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


XMLISH = re.compile(r"^\s*(<\?xml|<!DOCTYPE\s|<[A-Za-z_][\w.:-]*[\s/>])")
NS = re.compile(r"^\{[^}]*\}")   # ElementTree spells tags as {namespace}tag


def _tag(el):
    return NS.sub("", el.tag) if isinstance(el.tag, str) else "?"


def _row(el):
    """An element as a flat dict: attributes plus leaf children."""
    row = {k: v for k, v in el.attrib.items()}
    for child in el:
        if len(child) == 0:
            row[_tag(child)] = (child.text or "").strip()
    if not row:
        row = {_tag(el): (el.text or "").strip()}
    return row


def xml_to_toon(text):
    """(result, mode). The largest group of same-tag siblings becomes a TOON
    table; anything else is only minified — and says so, because reporting a
    minify as a TOON conversion is exactly the kind of flattering label this
    project is trying to get rid of.

    XML repeats every tag name on every record, so a uniform listing is where
    the bulk of the bytes are — same idea as the JSON path."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(text)

    best = (0, None, "")
    for parent in root.iter():
        groups = {}
        for child in parent:
            groups.setdefault(_tag(child), []).append(child)
        for tag, els in groups.items():
            # Only worth tabulating when the records actually have fields.
            if len(els) > best[0] and len(els) >= 2 and any(len(e) or e.attrib
                                                            for e in els):
                best = (len(els), els, tag)

    n, els, name = best
    if not els:
        return minify_xml(text), "minified XML (no uniform listing to tabulate)"
    rows = [_row(e) for e in els]
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    lines = [f"{name}[{n}]{{{','.join(keys)}}}:"]
    for r in rows:
        lines.append(" " + ",".join(cell(r.get(k, "")) for k in keys))
    return "\n".join(lines), f"XML -> TOON ({n} <{name}> records)"


def minify_xml(text):
    """Non-uniform XML: drop comments and the indentation between tags."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r">\s+<", "><", text).strip()


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
    # --keep script,css: preserve what the analysis targets (JS and/or CSS);
    # aliases: js -> script, style -> css.
    keep, files, i = set(), [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--keep" and i + 1 < len(argv):
            keep |= {t.strip().lower() for t in argv[i + 1].split(",")}
            i += 2
            continue
        if a.startswith("--keep="):
            keep |= {t.strip().lower() for t in a[7:].split(",")}
            i += 1
            continue
        files.append(a)
        i += 1
    keep = {{"js": "script", "style": "css"}.get(k, k) for k in keep}

    # Read bytes: the type has to be sniffed before anything is decoded.
    if files:
        try:
            with open(files[0], "rb") as f:
                blob = f.read()
        except OSError as e:
            print(f"cannot read {files[0]}: {e}", file=sys.stderr)
            return
    elif not sys.stdin.isatty():
        blob = sys.stdin.buffer.read()
    else:
        print("usage: claude-token toon [--keep script,css] <file>   (or pipe via stdin)\n"
              "       JSON/XML listing -> TOON; HTML -> stripped DOM (--keep "
              "preserves JS/CSS when analyzing those); .docx -> plain text; "
              "log/text -> repeated lines become [xN]",
              file=sys.stderr)
        return
    if not blob.strip():
        return

    kind = sniff(blob)
    if kind == "pdf":
        # No stdlib PDF text extraction, and guessing would be worse than
        # saying so: the Read tool already handles PDFs by page range.
        print("[toon] This is a PDF — nothing here can compact it. Read it by "
              "page range instead (Read with pages=\"2-5\"; each page costs "
              "~1.5-3k tokens), or run `pdftotext file.pdf - | claude-token "
              "toon` if poppler-utils is installed.", file=sys.stderr)
        return
    if kind in ("pptx", "xlsx", "zip"):
        print(f"[toon] {kind} is a binary container and is not supported "
              "(only .docx is). Export it to text/CSV first.", file=sys.stderr)
        return
    if kind == "binary":
        print("[toon] Binary input — refusing. Compacting it would produce "
              "mojibake and a meaningless savings figure.", file=sys.stderr)
        return

    # Size of the real source, for the honest before/after below.
    source_bytes = len(blob)
    if kind == "docx":
        raw = docx_text(blob)
        result, mode = compact_log(raw), "docx text"
    else:
        raw = blob.decode("utf-8", "replace").strip()
        try:
            result = to_toon(json.loads(raw))
            mode = "TOON"
        except json.JSONDecodeError:
            if HTMLISH.search(raw[:2000]):
                result = strip_dom(raw, keep)
                mode = "stripped DOM" + (f" (kept: {', '.join(sorted(keep))})"
                                         if keep else "")
            elif XMLISH.match(raw[:200]):
                try:
                    result, mode = xml_to_toon(raw)
                except Exception:  # malformed XML: minify, never mangle
                    result, mode = minify_xml(raw), "minified XML (unparseable)"
            else:
                result = compact_log(raw)
                mode = "compacted log"

    print(result, flush=True)  # flush so stats (stderr) come after the content
    # Stats go to stderr so stdout can be piped straight to the clipboard.
    # English on purpose: via /token this line reaches the model's context.
    after = len(result)
    if kind == "docx":
        # A .docx has no "before" token count — it was never text. Report the
        # result only, rather than inventing a percentage against zip bytes.
        print(f"\n[{mode}] {source_bytes:,} bytes of .docx -> ~{after // 4} "
              "tokens of text — paste the output above into the prompt.",
              file=sys.stderr)
    else:
        before = len(raw)
        pct = (1 - after / before) * 100 if before else 0
        print(f"\n[{mode}] ~{before // 4} -> ~{after // 4} tokens "
              f"({pct:.0f}% smaller) — paste the output above into the prompt.",
              file=sys.stderr)
