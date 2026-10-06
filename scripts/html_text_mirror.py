"""html_text_mirror - plain-text copies of datasets/**/*.html for qmd.

qmd indexes markdown only. This rebuilds datasets/.qmd-html/<rel>.html.md (one per
page, frontmatter points at the source) so context-search can find HTML artifacts.
The page's first text line (its <title>, else its first heading) becomes the
mirror's title. Run nightly before `qmd update`.
Usage: python3 scripts/html_text_mirror.py
"""
import json
import os
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PM_OS_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
import html_artifact_lib  # noqa: E402

MIRROR = ".qmd-html"


def _render(rel, text):
    source = "datasets/" + rel.replace(os.sep, "/")
    title, _, rest = text.partition("\n")
    if not title:
        return f"---\nsource: {source}\n---\n"
    # json.dumps yields a valid YAML double-quoted scalar.
    return (f"---\nsource: {source}\ntitle: {json.dumps(title)}\n---\n\n"
            f"# {title}\n\n{rest}\n")


def build(root=PM_OS_DIR):
    """Rebuild the mirror under <root>/datasets/.qmd-html. Returns pages written."""
    ds = os.path.join(root, "datasets")
    out_root = os.path.join(ds, MIRROR)
    shutil.rmtree(out_root, ignore_errors=True)
    count = 0
    for dirpath, dirnames, filenames in os.walk(ds):
        dirnames[:] = [d for d in dirnames if d != MIRROR and not d.startswith(".")]
        for fn in filenames:
            if not html_artifact_lib.is_html_path(fn):
                continue
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, ds)
            with open(src, encoding="utf-8", errors="replace") as f:
                text = html_artifact_lib.html_to_text(f.read())
            dest = os.path.join(out_root, rel + ".md")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(_render(rel, text))
            count += 1
    return count


if __name__ == "__main__":
    print(f"html_text_mirror: {build()} pages")
