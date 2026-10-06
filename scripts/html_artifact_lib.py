"""html_artifact_lib - the one place the engine's HTML-artifact rules live.

Rendering, dispatch, chat, judge, and search all import from here so the rules
can't drift. Pure functions, stdlib only. Runtime text is ASCII-safe.
"""
import re
from html.parser import HTMLParser

FORMATS = ("md", "html")
CONTRACT_SKILL = ".claude/skills/context-html-artifact/SKILL.md"
STYLE_PROFILE = "profile/voice/html.md"

# Served on /artifact/<id>. No allow-same-origin: the page runs in an opaque
# origin and can't read the board's API; connect-src 'none' also blocks fetch
# because the server sends Access-Control-Allow-Origin: *.
CSP = (
    "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox; "
    "default-src 'none'; "
    "style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com data:; "
    "img-src https: data:; "
    "script-src 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
    "connect-src 'none'"
)

_HTML_ASK = re.compile(r"\bas\s+(?:an?\s+)?(?:html(?:\s+page)?|page|web\s*page)\b(?!-|\s+(?:break|\d|entit|e-?mail|in\b|on\b))", re.I)


def is_html_path(path):
    """True when the path ends in .html or .htm (case/whitespace-insensitive)."""
    p = (path or "").strip().lower()
    return p.endswith(".html") or p.endswith(".htm")


def detect_output_format(title, description=""):
    """'html' when the ask says so explicitly ("as HTML", "as a page"), else None."""
    text = f"{title or ''}\n{description or ''}"
    return "html" if _HTML_ASK.search(text) else None


def resolve_output_format(task_fm, worker):
    """Task field wins, then the worker's default, then md."""
    for src in (task_fm, worker):
        if not isinstance(src, dict):
            continue
        v = str(src.get("output_format") or "").strip().lower()
        if v in FORMATS:
            return v
    return "md"


def dispatch_block():
    """Prompt suffix for HTML tasks; starts with blank lines since callers append it to a prompt."""
    return (
        "\n\nOUTPUT FORMAT: HTML\n"
        "This task's deliverable is a single self-contained .html file, not markdown.\n"
        f"- Before writing, read {CONTRACT_SKILL} (the technical contract) and "
        f"{STYLE_PROFILE} (the operator's visual style) and follow both.\n"
        "- Write it under the appropriate datasets/ directory and complete with "
        "--output pointing at the .html path.\n"
    )


def chat_hint(path):
    """Chat preamble telling the agent to edit the HTML file at path in place."""
    return (
        f"This task's output is an HTML page at {path}. When asked to change it, "
        "make targeted edits to that file in place with the Edit tool - never "
        "regenerate or rewrite the whole file, and never save a new copy. Keep to "
        f"{CONTRACT_SKILL} and {STYLE_PROFILE}.\n\n"
    )


class _Text(HTMLParser):
    _SKIP = {"style", "script", "noscript", "svg", "template"}
    _BLOCK = {"p", "div", "section", "header", "footer", "li", "tr", "h1", "h2",
              "h3", "h4", "h5", "h6", "br", "table", "ul", "ol", "blockquote", "article",
              "dt", "dd", "dl", "nav", "main", "aside", "figure", "figcaption", "hr",
              "pre", "details", "summary", "title"}
    _CELL = {"td", "th"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.out.append("\n")
        elif tag in self._CELL and not self._skip:
            self.out.append(" ")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_to_text(src):
    """Visible text of an HTML document, one block per line, for search and judging."""
    p = _Text()
    p.feed(src or "")
    p.close()
    lines = (re.sub(r"\s+", " ", ln).strip() for ln in "".join(p.out).split("\n"))
    return "\n".join(ln for ln in lines if ln)
