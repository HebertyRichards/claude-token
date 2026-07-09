"""RAG-style retrieval: BM25 index over project code chunks so Claude reads
only the relevant line ranges instead of dumping whole files into context.
Pure stdlib and offline — lexical ranking, no embeddings."""
import hashlib
import json
import math
import os
import re
import time

from common import CLAUDE_DIR, save_json

RAG_DIR = os.path.join(CLAUDE_DIR, "claude-token-rag")
EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp",
        ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".lua", ".sh", ".sql",
        ".html", ".xhtml", ".twig", ".css", ".scss", ".sass", ".md", ".json",
        ".yaml", ".yml", ".toml"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", "out",
             ".venv", "venv", "target", ".idea", ".vscode", "vendor"}
CHUNK, STEP = 40, 30          # lines per chunk / stride (10-line overlap)
MAX_FILE = 1_000_000          # skip files bigger than ~1 MB
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")
CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenize(text):
    toks = []
    for w in WORD.findall(text):
        for part in CAMEL.sub(" ", w).replace("_", " ").split():
            if len(part) > 2:
                toks.append(part.lower())
    return toks


def index_path(root):
    h = hashlib.sha1(os.path.abspath(root).encode()).hexdigest()[:16]
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
            if os.path.splitext(name)[1] not in EXTS:
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > MAX_FILE:
                    continue
                with open(full, encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            except OSError:
                continue
            files += 1
            rel = os.path.relpath(full, root)
            for start in range(0, max(len(lines), 1), STEP):
                block = lines[start:start + CHUNK]
                if not any(l.strip() for l in block):
                    continue
                tf = {}
                for t in tokenize("\n".join(block)):
                    tf[t] = tf.get(t, 0) + 1
                if not tf:
                    continue
                for t in tf:
                    df[t] = df.get(t, 0) + 1
                chunks.append({"f": rel, "s": start + 1,
                               "e": min(start + CHUNK, len(lines)), "tf": tf})
                if start + CHUNK >= len(lines):
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
    scored.sort(key=lambda x: -x[0])
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
        print(f"{c['f']}:{c['s']}-{c['e']}  (score {score:.1f})")


def hook_hits(cwd, prompt):
    """Top matches formatted for hook injection; [] when no index/matches."""
    idx = find_index(cwd)
    if not idx:
        return []
    return [f"{c['f']}:{c['s']}-{c['e']}" for _s, c in search(idx, prompt, top=3)]
