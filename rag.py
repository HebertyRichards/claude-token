"""RAG-style retrieval: BM25 index over project code chunks so Claude reads
only the relevant line ranges instead of dumping whole files into context.
Pure stdlib and offline — lexical ranking, no embeddings."""
import ast
import hashlib
import json
import math
import os
import re
import time
import unicodedata

from common import CLAUDE_DIR, save_json

RAG_DIR = os.path.join(CLAUDE_DIR, "claude-token-rag")
EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp",
        ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".lua", ".sh", ".sql",
        ".html", ".xhtml", ".twig", ".css", ".scss", ".sass", ".md", ".json",
        ".yaml", ".yml", ".toml"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", "out",
             ".venv", "venv", "target", ".idea", ".vscode", "vendor"}
# Never index secret-bearing files: the index itself only stores token
# frequencies, but a hit would tell Claude to READ the file — putting the
# secret into the context and the transcript.
SECRET_FILE = re.compile(
    r"(^|[._-])(secrets?|credentials?|passwords?|passwd|apikeys?|api_keys?)"
    r"([._-]|$)|^\.env", re.I)
CHUNK, STEP = 40, 30          # lines per chunk / stride (10-line overlap)
MAX_FILE = 1_000_000          # skip files bigger than ~1 MB
WORD = re.compile(r"[^\W\d]\w+")
CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# Words that say nothing about where the logic is. Left in, a request written
# in plain Portuguese or English matches every comment and message written in
# that language, and prose-heavy files win the ranking.
STOP = frozenset("""
    the and for with that this from not are was has have you your its into when
    then than what which how does can que para com uma por nao dos das como mais
    mas tem ser esta isso essa esse sem seu sua aos nos nas pelo pela quando onde
    qual quais entre sobre depois antes cada todo toda ele ela sao foi
    file files arquivo arquivos code codigo""".split())
SMALL_FILE = 150    # lines: a file this size gets read whole anyway
NAMED = re.compile(r"[\w./\\-]+\.[A-Za-z]{1,5}\b")


def _fold(text):
    """Lower-case, accents dropped: "função" and "funcao" are one word."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch)).lower()


def tokenize(text):
    toks = []
    for w in WORD.findall(text):
        for part in CAMEL.sub(" ", w).replace("_", " ").split():
            part = _fold(part)
            if len(part) > 2 and part not in STOP:
                toks.append(part)
    return toks


# Logic decides, commentary only helps find the spot. Comments, docstrings and
# docs say what the code was meant to do when they were written; the code is
# what it does now, and the two drift apart. So a token counts in full when it
# comes from logic and at COMMENT_WEIGHT when it comes from commentary, and a
# chunk that is ONLY commentary (a doc page, a header comment) never outranks
# one whose code matches — see search(). The text inside string literals and
# the whole of a data file count as commentary too: a translation table or an
# e-mail template is prose about the product, and by sheer word count it beat
# the function that implements the thing.
COMMENT_WEIGHT = 0.3
DOC_EXTS = {".md"}
DATA_EXTS = {".json", ".yaml", ".yml", ".toml"}
HASH_COMMENTS = {".py", ".sh", ".rb", ".yaml", ".yml", ".toml"}
SLASH_COMMENTS = {".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp",
                  ".hpp", ".cs", ".go", ".rs", ".php", ".scss", ".sass"}


def _scan(text, line_marks, block):
    """Split source into (logic, commentary), character by character, keeping
    every newline in both so line numbers stay aligned. Knows strings, so a
    marker inside one ("http://…", "a # b") is not a comment."""
    logic, comment = [], []
    quote, in_line, in_block = "", False, False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            logic.append(ch)
            comment.append(ch)
            in_line = False
            if quote in ("'", '"'):     # an unclosed quote ends with its line
                quote = ""
        elif in_line:
            comment.append(ch)
        elif in_block:
            if text.startswith(block[1], i):
                in_block = False
                i += len(block[1])
                continue
            comment.append(ch)
        elif quote:
            if ch == quote:
                quote = ""
                logic.append(ch)
            else:           # the text of a string is prose, not logic
                comment.append(ch)
                if ch == "\\" and i + 1 < n and text[i + 1] != "\n":
                    comment.append(text[i + 1])
                    i += 1
        elif ch in "\"'`":
            quote = ch
            logic.append(ch)
        elif block and text.startswith(block[0], i):
            in_block = True
            i += len(block[0])
            continue
        elif any(text.startswith(mark, i) for mark in line_marks):
            in_line = True
        else:
            logic.append(ch)
        i += 1
    return "".join(logic), "".join(comment)


def _docstrings_out(text, logic, comment):
    """Python docstrings are commentary too: move their lines across."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return logic, comment
    source, lo, co = text.split("\n"), logic.split("\n"), comment.split("\n")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            for i in range(first.lineno - 1, min(first.end_lineno, len(source))):
                lo[i], co[i] = "", source[i]
    return "\n".join(lo), "\n".join(co)


def split_logic(text, ext):
    """(logic, commentary) of a file's text, line for line."""
    blank = "\n" * text.count("\n")
    if ext in DOC_EXTS or ext in DATA_EXTS:
        return blank, text
    if ext in SLASH_COMMENTS:
        return _scan(text, ("//",), ("/*", "*/"))
    if ext == ".css":
        return _scan(text, (), ("/*", "*/"))
    if ext in (".sql", ".lua"):
        return _scan(text, ("--",), ("/*", "*/") if ext == ".sql" else None)
    if ext in HASH_COMMENTS:
        logic, comment = _scan(text, ("#",), None)
        return _docstrings_out(text, logic, comment) if ext == ".py" else (logic, comment)
    return text, blank


def index_path(root):
    # normcase: a no-op on POSIX. On Windows the same folder arrives as
    # C:\Users\x\Documentos or c:/users/x/documentos depending on who reports
    # it, and a case-sensitive hash would never find the index again.
    key = os.path.normcase(os.path.abspath(root))
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    return os.path.join(RAG_DIR, h + ".json")


def find_index(cwd):
    """Index for cwd or the nearest indexed ancestor directory."""
    d = os.path.abspath(cwd or os.getcwd())
    while True:
        p = index_path(d)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def cmd_index(argv):
    root = os.path.abspath(argv[0]) if argv else os.getcwd()
    chunks, df = [], {}
    files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                       and not d.startswith(".")]
        for name in filenames:
            if os.path.splitext(name)[1] not in EXTS or SECRET_FILE.search(name):
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > MAX_FILE:
                    continue
                with open(full, encoding="utf-8", errors="replace") as f:
                    text = f.read().replace("\r\n", "\n").replace("\r", "\n")
            except OSError:
                continue
            files += 1
            # "/" everywhere: the pointer handed to the model reads the same on
            # Windows as on Linux, and "/" is a valid separator on both.
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            ext = os.path.splitext(name)[1]
            if text.endswith("\n"):
                text = text[:-1]
            logic, comment = (part.split("\n") for part in split_logic(text, ext))
            total = len(logic)
            for start in range(0, max(total, 1), STEP):
                in_logic = tokenize("\n".join(logic[start:start + CHUNK]))
                tf = {}
                for t in in_logic:
                    tf[t] = tf.get(t, 0) + 1
                for t in tokenize("\n".join(comment[start:start + CHUNK])):
                    tf[t] = round(tf.get(t, 0) + COMMENT_WEIGHT, 2)
                if tf:
                    for t in tf:
                        df[t] = df.get(t, 0) + 1
                    chunk = {"f": rel, "s": start + 1,
                             "e": min(start + CHUNK, total), "tf": tf}
                    if not in_logic:
                        # Nothing but commentary here: a doc page, a header
                        # comment, a docstring. Ranked after every code match.
                        chunk["k"] = ("doc" if ext in DOC_EXTS else
                                      "data" if ext in DATA_EXTS else "comment")
                    chunks.append(chunk)
                if start + CHUNK >= total:
                    break

    os.makedirs(RAG_DIR, exist_ok=True)
    save_json(index_path(root), {"root": root, "built": time.time(),
                                 "df": df, "chunks": chunks})
    print(f"indexados {files} arquivos, {len(chunks)} trechos -> {index_path(root)}")
    print("rode `claude-token index` de novo após mudanças grandes no código.")


def search(idx, query, top=3):
    """BM25 ranking of chunks for a free-text query."""
    q = set(tokenize(query))
    if not q or not idx or not idx.get("chunks"):
        return []
    n = len(idx["chunks"])
    df = idx["df"]
    avg_len = sum(sum(c["tf"].values()) for c in idx["chunks"]) / n
    k1, b = 1.5, 0.75
    scored = []
    for c in idx["chunks"]:
        dl = sum(c["tf"].values())
        s = 0.0
        for t in q:
            f = c["tf"].get(t)
            if not f:
                continue
            idf = math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avg_len))
        if s > 0:
            scored.append((s, c))
    # Code that matches comes before anything that is only commentary, however
    # well the commentary scores: a README repeating the query ten times is
    # still a description, and may be a stale one.
    scored.sort(key=lambda x: (bool(x[1].get("k")), -x[0]))
    return scored[:top]


def cmd_search(argv):
    if not argv:
        print("uso: claude-token search <consulta>")
        return
    idx = find_index(os.getcwd())
    if not idx:
        print("sem índice para esta pasta — rode `claude-token index` primeiro.")
        return
    hits = search(idx, " ".join(argv), top=5)
    if not hits:
        print("nada encontrado.")
        return
    for score, c in hits:
        kind = {"doc": "  [documentação]", "comment": "  [só comentário]",
                "data": "  [arquivo de dados]"}.get(c.get("k"), "")
        print(f"{c['f']}:{c['s']}-{c['e']}  (score {score:.1f}){kind}")


def _named_files(idx, prompt):
    """Indexed files the request mentions by path or by name."""
    files = {c["f"] for c in idx["chunks"]}
    named = set()
    for raw in NAMED.findall(prompt):
        want = raw.replace("\\", "/").lstrip("./").lower()
        named |= {f for f in files
                  if f.lower() == want or f.lower().endswith("/" + want)}
    return named


def _merge(hits):
    """One pointer per stretch of a file: ranges that overlap or touch become
    one, instead of three pointers into the same eighty lines."""
    out = []
    for _score, c in hits:
        for item in out:
            if item[0] == c["f"] and c["s"] <= item[2] + 1 and c["e"] >= item[1] - 1:
                item[1], item[2] = min(item[1], c["s"]), max(item[2], c["e"])
                break
        else:
            out.append([c["f"], c["s"], c["e"], c.get("k")])
    return out


def hook_hits(cwd, prompt):
    """Pointers worth injecting, or [] — and [] is the common, correct answer.
    Every pointer costs tokens on every later turn, and a wrong one sends the
    model reading the wrong file, so this only speaks when it has something."""
    idx = find_index(cwd)
    if not idx or not idx.get("chunks"):
        return []
    named = _named_files(idx, prompt)
    if named:
        # The user already said where to look. A small file is read whole, so
        # a pointer adds nothing; in a big one the best ranges are worth it.
        size = {}
        for c in idx["chunks"]:
            size[c["f"]] = max(size.get(c["f"], 0), c["e"])
        big = {f for f in named if size.get(f, 0) > SMALL_FILE}
        hits = search(dict(idx, chunks=[c for c in idx["chunks"] if c["f"] in big]),
                      prompt, top=2) if big else []
    else:
        hits = search(idx, prompt, top=3)
        query = set(tokenize(prompt))
        # The best match has to be code, and to match more than one word of
        # the request: one shared word is a coincidence, not a location.
        if (not hits or hits[0][1].get("k")
                or sum(1 for t in query if hits[0][1]["tf"].get(t)) < min(2, len(query))):
            return []
        hits = [h for h in hits if not h[1].get("k") and h[0] >= hits[0][0] * 0.5]
    return [f"{f}:{s}-{e}" + (" (not code: may be stale)" if kind else "")
            for f, s, e, kind in _merge(hits)]
