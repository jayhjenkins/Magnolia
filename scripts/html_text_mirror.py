"""html_text_mirror - plain-text copies of datasets/**/*.html for qmd.

qmd indexes markdown only. This rebuilds datasets/.qmd-html/<rel>.html.md (one per
page, frontmatter points at the source) so context-search can find HTML artifacts.
The page's first text line (its <title>, else its first heading) becomes the
mirror's title. Run nightly before `qmd update`.

The mirror is built in datasets/.qmd-html.tmp and swapped in only after the walk,
so a bad page never leaves a half-built index. Unreadable pages are warned about
and skipped; symlinked pages and pages over MAX_BYTES are skipped.
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
MAX_BYTES = 5 * 1024 * 1024


def _yaml_str(value):
    # A JSON string is a valid YAML double-quoted scalar.
    return json.dumps(value, ensure_ascii=False)


def _render(rel, text):
    source = "datasets/" + rel.replace(os.sep, "/")
    title, _, rest = text.partition("\n")
    if not title:
        return f"---\nsource: {_yaml_str(source)}\n---\n"
    return (f"---\nsource: {_yaml_str(source)}\ntitle: {_yaml_str(title)}\n---\n\n"
            f"# {title}\n\n{rest}\n")


def _warn(msg):
    print(f"html_text_mirror: warning: {msg}".encode("ascii", "replace").decode("ascii"))


def _refuse_symlink(path):
    if os.path.islink(path):
        raise RuntimeError(f"html_text_mirror: refusing to write through symlink {path}")


def build(root=PM_OS_DIR):
    """Rebuild the mirror under <root>/datasets/.qmd-html. Returns pages written."""
    ds = os.path.join(root, "datasets")
    out_root = os.path.join(ds, MIRROR)
    tmp_root = out_root + ".tmp"
    _refuse_symlink(out_root)
    _refuse_symlink(tmp_root)
    shutil.rmtree(tmp_root, ignore_errors=True)
    os.makedirs(tmp_root)
    count = 0
    for dirpath, dirnames, filenames in os.walk(ds):
        # Dot-dirs are skipped, which covers both .qmd-html and .qmd-html.tmp.
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if not html_artifact_lib.is_html_path(fn):
                continue
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, ds)
            if os.path.islink(src):
                continue
            try:
                if os.path.getsize(src) > MAX_BYTES:
                    _warn(f"skipped {rel} (over {MAX_BYTES} bytes)")
                    continue
                with open(src, encoding="utf-8", errors="replace") as f:
                    text = html_artifact_lib.html_to_text(f.read())
                dest = os.path.join(tmp_root, rel + ".md")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(_render(rel, text))
            except OSError as e:
                _warn(f"skipped {rel} ({type(e).__name__})")
                continue
            count += 1
    shutil.rmtree(out_root, ignore_errors=True)
    os.replace(tmp_root, out_root)
    return count


if __name__ == "__main__":
    print(f"html_text_mirror: {build()} pages")
