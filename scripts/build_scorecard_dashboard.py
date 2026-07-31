#!/usr/bin/env python3
"""Record scorecard results into values.json and regenerate dashboard.html."""
import argparse, json, datetime, html as html_mod
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCORECARD = ROOT / "datasets" / "scorecard"
VALUES = SCORECARD / "values.json"
REGISTRY = SCORECARD / "registry.json"
DASHBOARD = SCORECARD / "dashboard.html"
SHOW_WEEKS = 8


def fmt_val(value, fmt, pct_of_val=None):
    if value is None:
        return "", ""
    if fmt == "k":
        s = f"{value/1000:.1f}k"
    elif fmt == "pct":
        s = f"{value*100:.1f}%"
    elif fmt == "int":
        s = str(int(value))
    elif fmt == "ratio1":
        s = f"{value:.1f}"
    else:
        s = str(value)
    extra = ""
    if pct_of_val and pct_of_val > 0 and value is not None:
        extra = f'<span class="pof">{value/pct_of_val*100:.1f}%</span>'
    return s, extra


def ns_class(metric, value):
    t = metric.get("target")
    if t is None or value is None:
        return ""
    hb = metric.get("higher_better", True)
    ratio = value / t if t else 0
    if hb:
        if ratio >= 1.0:
            return "good"
        elif ratio >= 0.9:
            return "warn"
        return "bad"
    else:
        if ratio <= 1.0:
            return "good"
        elif ratio <= 1.1:
            return "warn"
        return "bad"


def build_chart_svg(weeks_sorted, values, slug, target, width=680, height=170):
    vals = []
    labels = []
    for w in weeks_sorted:
        v = values.get(w, {}).get(slug, {}).get("value")
        if v is not None:
            vals.append(v)
            labels.append(w[5:])
        else:
            vals.append(None)
            labels.append(w[5:])
    filtered = [(l, v) for l, v in zip(labels, vals) if v is not None]
    if len(filtered) < 2:
        return '<div class="chart-empty">Not enough data</div>'
    fl, fv = zip(*filtered)
    mn, mx = min(fv), max(fv)
    if target:
        mn = min(mn, target)
        mx = max(mx, target)
    pad = (mx - mn) * 0.15 or 1
    mn -= pad
    mx += pad
    lm, rm, tm, bm = 46, 14, 12, 26
    pw = width - lm - rm
    ph = height - tm - bm

    def x(i):
        return lm + (i / max(len(fv) - 1, 1)) * pw

    def y(v):
        return tm + ph - ((v - mn) / (mx - mn)) * ph

    parts = [f'<svg class="chart" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    if target:
        ty = y(target)
        parts.append(f'<line x1="{lm}" y1="{ty:.1f}" x2="{width-rm}" y2="{ty:.1f}" class="tline"/>')
        parts.append(f'<text x="{width-rm}" y="{ty-5:.1f}" class="tlab" text-anchor="end">target {fmt_val(target, "k")[0]}</text>')
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(fv))
    parts.append(f'<polyline points="{pts}" class="cline" fill="none"/>')
    for i, v in enumerate(fv):
        parts.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="2.6"/>')
    lv = fv[-1]
    parts.append(f'<text x="{x(len(fv)-1):.1f}" y="{y(lv)-8:.1f}" class="clatest" text-anchor="end">{fmt_val(lv, "k")[0]}</text>')
    parts.append(f'<text x="{lm}" y="{height-5}" class="xlab">{fl[0]}</text>')
    parts.append(f'<text x="{width-rm}" y="{height-5}" class="xlab" text-anchor="end">{fl[-1]}</text>')
    parts.append(f'<text x="{lm-6}" y="{y(mx+pad*0.3):.1f}" class="ylab" text-anchor="end">{fmt_val(mx, "k")[0]}</text>')
    parts.append(f'<text x="{lm-6}" y="{y(mn-pad*0.3):.1f}" class="ylab" text-anchor="end">{fmt_val(mn, "k")[0]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def render(values_data, registry):
    metrics = registry["metrics"]
    metric_map = {m["slug"]: m for m in metrics}
    all_weeks = sorted(values_data["weeks"].keys())
    show_weeks = all_weeks[-SHOW_WEEKS:] if len(all_weeks) > SHOW_WEEKS else all_weeks
    latest = show_weeks[-1] if show_weeks else ""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    ns_metrics = [m for m in metrics if m.get("section") == "northstar"]
    sc_metrics = [m for m in metrics if m.get("section") != "northstar"]
    ordered = sorted(metrics, key=lambda m: m.get("order", 99))

    css = """body{font:14px -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1419;color:#e6e9ef}
 .wrap{max-width:1180px;margin:0 auto;padding:26px}
 h1{font-size:20px;margin:0 0 2px} .sub{color:#8b97a8;font-size:12px;margin-bottom:18px}
 h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#8b97a8;margin:22px 0 10px}
 .nsbar{display:flex;gap:18px;align-items:center;flex-wrap:wrap;background:#161c25;border:1px solid #1e2630;border-radius:12px;padding:16px}
 .ns{min-width:150px} .ns-name{color:#8b97a8;font-size:12px}
 .ns-val{font-size:30px;font-weight:700;margin:2px 0} .ns-tgt{color:#8b97a8;font-size:12px}
 .ns.good .ns-val{color:#5fd38a} .ns.warn .ns-val{color:#f5c451} .ns.bad .ns-val{color:#f08a8a}
 .chart{flex:1;min-width:360px} .chart-empty{color:#8b97a8;font-size:12px}
 .cline{stroke:#5b8def;stroke-width:2} circle{fill:#5b8def}
 .tline{stroke:#5fd38a;stroke-dasharray:4 3;stroke-width:1} .tlab{fill:#5fd38a;font-size:10px}
 .clatest{fill:#e6e9ef;font-size:12px;font-weight:700} .xlab,.ylab{fill:#8b97a8;font-size:10px}
 .tblwrap{overflow-x:auto;border:1px solid #1e2630;border-radius:10px}
 table{border-collapse:collapse;width:100%;font-size:13px}
 th,td{padding:8px 10px;border-bottom:1px solid #1e2630;text-align:right;white-space:nowrap}
 th.mh,td.mname{text-align:left;position:sticky;left:0;z-index:2;background:#12171e;border-right:1px solid #2a3340}
 thead th{position:sticky;top:0;background:#11161d;color:#8b97a8;font-weight:600;font-size:11px}
 thead th.mh{z-index:3;background:#11161d}
 .tw{color:#5a6675;margin-right:6px;font-size:10px}
 th.wk{text-align:right} th.wk .yr{display:block;color:#5a6675;font-weight:400}
 th.wk.latest{color:#e6e9ef;background:#16202c}
 .colcopy{display:block;margin-top:4px;background:#1b2230;color:#aeb8c7;border:1px solid #2a3340;
   border-radius:5px;padding:2px 6px;cursor:pointer;font:10px monospace}
 .colcopy:hover{background:#243047}
 td.mname{font-weight:600} .src{display:inline-block;margin-left:7px;font-size:9px;font-weight:400;
   padding:1px 5px;border-radius:8px;color:#8b97a8;background:#1b222c;text-transform:uppercase}
 .src.manual{color:#f5c451;background:#332a12} .src.deferred{color:#7e8794;background:#23262c}
 .dtoggle{cursor:pointer;color:#5a6675} .defer{color:#5a6675}
 .pof{display:block;color:#8b97a8;font-size:10px}
 td.cell.latest{background:#141c26}
 input.hoai{width:64px;background:#11161d;color:#f5c451;border:1px solid #3a3320;border-radius:5px;
   padding:4px 6px;text-align:right;font:13px inherit}
 tr.defrow{display:none} tr.defrow.show{display:table-row}
 tr.defrow td{text-align:left;color:#aeb8c7;font-size:12px;background:#11161d;white-space:normal}
 .hint{color:#8b97a8;font-size:12px;margin-top:14px}"""

    h = [f'<!doctype html><html><head><meta charset="utf-8">',
         f'<title>Resident Experience Scorecard — trend</title>',
         f'<style>\n {css}\n</style></head><body><div class="wrap">',
         f' <h1>Resident Experience Scorecard</h1>',
         f' <div class="sub">Trend view · current week <b>{latest}</b> · generated {now} · showing last {len(show_weeks)} of {len(all_weeks)} weeks</div>']

    h.append(' <h2>North Star</h2>')
    ns_parts = [' <div class="nsbar">']
    for m in ns_metrics:
        v = values_data["weeks"].get(latest, {}).get(m["slug"], {}).get("value")
        disp, _ = fmt_val(v, m["format"])
        cls = ns_class(m, v)
        tgt = m.get("target_display", "")
        ns_parts.append(f'<div class="ns {cls}"><div class="ns-name">{html_mod.escape(m["name"])}</div>'
                        f'<div class="ns-val">{disp}</div>'
                        f'<div class="ns-tgt">target {tgt}</div></div>')
    hw_m = metric_map.get("home-wau")
    if hw_m:
        ns_parts.append(build_chart_svg(all_weeks, values_data["weeks"], "home-wau",
                                        hw_m.get("target")))
    ns_parts.append('</div>')
    h.extend(ns_parts)

    h.append(' <h2>Scorecard — weekly trend</h2>')
    ncols = len(show_weeks) + 1
    h.append(' <div class="tblwrap"><table>')
    hdr = ['  <thead><tr><th class="mh">Metric</th>']
    for w in show_weeks:
        lcls = " latest" if w == latest else ""
        mm_dd = w[5:]
        yr = w[:4]
        hdr.append(f'<th class="wk{lcls}">{mm_dd}<span class="yr">{yr}</span>'
                    f'<button class="colcopy" onclick="copyCol(\'{w}\',this)" '
                    f'title="Copy this week down (vertical paste)">copy &#8595;</button></th>')
    hdr.append('</tr></thead>')
    h.append("".join(hdr))

    h.append('  <tbody>')
    for m in ordered:
        slug = m["slug"]
        src = m.get("source", "auto")
        src_cls = "manual" if src == "manual" else ("deferred" if src == "deferred" else "auto")
        h.append(f'<tr class="mrow"><td class="mname" onclick="toggleDef(\'{slug}\')">'
                 f'<span class="tw">&#9656;</span>{html_mod.escape(m["name"])}'
                 f'<span class="src {src_cls}">{src}</span></td>')

        hw_vals = {}
        if m.get("show_pct_of"):
            ref_slug = m["show_pct_of"]
            for w in show_weeks:
                hw_vals[w] = values_data["weeks"].get(w, {}).get(ref_slug, {}).get("value")

        for w in show_weeks:
            lcls = " latest" if w == latest else ""
            entry = values_data["weeks"].get(w, {}).get(slug, {})
            v = entry.get("value")
            st = entry.get("status", "")

            if src == "deferred":
                h.append(f'<td class="cell{lcls}" data-week="{w}" data-copy=""><span class="defer">&middot;</span></td>')
            elif src == "manual":
                val_str = str(int(v)) if v is not None else ""
                h.append(f'<td class="cell{lcls}" data-week="{w}">'
                         f'<input class="hoai" data-week="{w}" value="{val_str}" '
                         f'placeholder="enter" oninput="saveHoai(this)"></td>')
            else:
                disp, extra = fmt_val(v, m["format"], hw_vals.get(w))
                copy_v = disp
                h.append(f'<td class="cell{lcls}" data-week="{w}" data-copy="{copy_v}">{disp}{extra}</td>')
        h.append('</tr>')
        defn = html_mod.escape(m.get("definition", ""))
        h.append(f'<tr class="defrow" id="def-{slug}"><td colspan="{ncols}">'
                 f'<b>How it&rsquo;s calculated:</b> {defn}</td></tr>')
    h.append('</tbody>')
    h.append(' </table></div>')
    h.append(' <div class="hint">Click a metric name to see how it&rsquo;s calculated. Click <b>copy &#8595;</b> in any week\n'
             ' header to copy that week&rsquo;s full column, then paste straight down a dated column in your L10 sheets.\n'
             ' The HOAi cell is editable &mdash; type the number; it&rsquo;s saved in this browser.</div>')
    h.append('</div>')

    h.append("""<script>
function toggleDef(s){var r=document.getElementById('def-'+s);if(r)r.classList.toggle('show');}
function saveHoai(i){try{localStorage.setItem('hoai_'+i.dataset.week,i.value);}catch(e){}}
function restore(){document.querySelectorAll('input.hoai').forEach(function(i){
  try{var v=localStorage.getItem('hoai_'+i.dataset.week);if(v!==null&&v!=='')i.value=v;}catch(e){}});}
function copyCol(wk,btn){
  var cells=document.querySelectorAll('td.cell[data-week="'+wk+'"]');
  var out=[];cells.forEach(function(c){var inp=c.querySelector('input');
    out.push(inp?inp.value:(c.dataset.copy||''));});
  navigator.clipboard.writeText(out.join('\\n'));
  var t=btn.innerHTML;btn.innerHTML='&#10003; copied';setTimeout(function(){btn.innerHTML=t;},1100);
}
window.addEventListener('load',restore);
</script></body></html>""")
    return "\n".join(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", required=True)
    ap.add_argument("--record", required=True)
    args = ap.parse_args()

    with open(args.record) as f:
        results = json.load(f)
    with open(VALUES) as f:
        values = json.load(f)
    with open(REGISTRY) as f:
        registry = json.load(f)

    if args.week in values["weeks"]:
        print(f"WARNING: week {args.week} already exists — merging (new values overwrite).")
    values["weeks"][args.week] = results
    with open(VALUES, "w") as f:
        json.dump(values, f, indent=2, ensure_ascii=False)
    print(f"Recorded {len(results)} metrics for week {args.week} into {VALUES}")

    html_out = render(values, registry)
    with open(DASHBOARD, "w") as f:
        f.write(html_out)
    print(f"Dashboard written to {DASHBOARD}")


if __name__ == "__main__":
    main()
