"""日次ページと一覧ページの見た目。外へ何も読みに行かない（フォントも CSS も同梱）。

中身はウィンドウタイトルを含むので、開くたびに外へ通信が飛ぶ作りにはしない。
色は変数で持ち、明るい画面と暗い画面で同じ変数名の値だけを入れ替える。
"""
import html as html_escape

_LIGHT = """
  --bg: #f3f4f1; --surface: #ffffff; --sunk: #eceee9;
  --ink: #1b211f; --muted: #5f6a65; --faint: #8f9994; --line: #dfe3df;
  --accent: #2c6b57; --warn: #a84a22;
  --k-present: #6fae93; --k-act: #22513f; --k-passive: #5a86b8;
  --k-idle: #d6d2c8; --k-locked: #b5b0a6; --k-sleep: #9a958c; --k-nodata: #e6e4de;
  --sleep: #4b4f9e; --phone: #dd7f1f; --auto: #8a5cc0; --bar: #4f9a7b; --chip: #eef1ee;
"""
_DARK = """
  color-scheme: dark;
  --bg: #101413; --surface: #171c1a; --sunk: #1d2321;
  --ink: #e1e7e4; --muted: #9ba7a1; --faint: #6c7772; --line: #29302d;
  --accent: #6cc3a0; --warn: #ec9a6e;
  --k-present: #3b7a61; --k-act: #8fdcbc; --k-passive: #5689c2;
  --k-idle: #39403c; --k-locked: #4a504c; --k-sleep: #5b615d; --k-nodata: #222825;
  --sleep: #9da2f2; --phone: #f2a24f; --auto: #c39af0; --bar: #4fa584; --chip: #222a27;
"""

CSS = (":root { color-scheme: light;" + _LIGHT
       + '  --font: "BIZ UDPGothic", "Yu Gothic UI", "Hiragino Sans", "Noto Sans JP", sans-serif;\n'
       + '  --num: "Bahnschrift", "DIN Alternate", "Segoe UI", sans-serif; }\n'
       + '@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {' + _DARK + "} }\n"
       + ':root[data-theme="dark"] {' + _DARK + "}\n" + """
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font-family: var(--font);
       font-size: 14px; line-height: 1.65; -webkit-text-size-adjust: 100%; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px;
                                         border-radius: 4px; }
code { font-family: Consolas, monospace; font-size: 12px; background: var(--sunk);
       padding: 1px 5px; border-radius: 4px; }
.wrap { max-width: 1080px; margin: 0 auto; padding-inline: 20px; padding-block: 0 72px; }

.top { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5;
       background: var(--bg); border-bottom: 1px solid var(--line); }
.top .wrap { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
             padding-block: 10px; }
.brand { font-weight: 700; letter-spacing: .04em; color: var(--ink); }
.nav { margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; }
.nav a, .nav span { padding: 3px 12px; border-radius: 999px; border: 1px solid var(--line);
                    background: var(--surface); font-size: 13px; color: var(--ink); }
.nav a:hover { border-color: var(--accent); text-decoration: none; }
.nav span { color: var(--faint); }

header.day { padding-block: 28px 4px; }
header.day h1 { font-size: 30px; line-height: 1.2; margin: 0; text-wrap: balance; }
header.day h1 small { font-size: 18px; color: var(--muted); margin-left: 8px; font-weight: 400; }
header.day .range { color: var(--muted); font-size: 13px; margin-top: 6px;
                    font-variant-numeric: tabular-nums; }

.stats { display: grid; grid-template-columns: repeat(4, 1fr);
         margin-block: 20px 4px; background: var(--surface); border: 1px solid var(--line);
         border-radius: 12px; overflow: hidden; }
.stat { padding: 12px 16px; border-right: 1px solid var(--line);
        border-bottom: 1px solid var(--line); margin: 0 -1px -1px 0; }
.stat .k { font-size: 12px; color: var(--muted); }
.stat .v { font-family: var(--num); font-size: 26px; line-height: 1.3; font-weight: 600;
           font-variant-numeric: tabular-nums; }
.stat .n { font-size: 11px; color: var(--faint); line-height: 1.45; }

section { margin-top: 40px; }
h2 { font-size: 18px; margin: 0 0 6px; }
h2 .total { font-family: var(--num); font-size: 15px; color: var(--muted); font-weight: 400;
            margin-left: 8px; font-variant-numeric: tabular-nums; }
.sub { color: var(--muted); font-size: 12px; margin: 0 0 12px; }
.muted { color: var(--faint); }
.warn { color: var(--warn); }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
         padding: 4px 16px 8px; }

.group + .group { margin-top: 18px; }
.group > h3 { font-size: 13px; letter-spacing: .06em; color: var(--muted); margin: 10px 0 2px;
              display: flex; gap: 10px; align-items: baseline; }
.group > h3 .t { font-family: var(--num); letter-spacing: 0; font-weight: 400;
                 font-variant-numeric: tabular-nums; }
details.proj { border-top: 1px solid var(--line); }
details.proj > summary { list-style: none; cursor: pointer; display: grid; align-items: center;
                         grid-template-columns: 12px minmax(8em, 1.2fr) minmax(60px, 2fr) 6.5em minmax(0, 1.2fr);
                         gap: 12px; padding-block: 9px; }
details.proj > summary::-webkit-details-marker { display: none; }
details.proj > summary::before { content: ""; width: 7px; height: 7px;
                                 border-right: 2px solid var(--faint);
                                 border-bottom: 2px solid var(--faint);
                                 transform: rotate(-45deg); transition: transform .15s; }
details.proj[open] > summary::before { transform: rotate(45deg); }
.proj .name { font-weight: 600; overflow-wrap: anywhere; }
.meter { height: 8px; border-radius: 4px; background: var(--sunk); overflow: hidden; }
.meter i { display: block; height: 100%; background: var(--bar); border-radius: 4px; }
.proj .time { font-family: var(--num); text-align: right; font-variant-numeric: tabular-nums; }
.chips { display: flex; gap: 4px; flex-wrap: wrap; justify-content: flex-end; }
.chip { font-size: 11px; padding: 0 8px; border-radius: 999px; background: var(--chip);
        color: var(--muted); white-space: nowrap; }
.chip.pr { color: var(--accent); }
.proj .body { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
              gap: 4px 28px; padding: 0 0 12px 24px; }
.proj h4 { font-size: 12px; color: var(--muted); margin: 4px 0 2px; }
.proj ul { margin: 0; padding: 0; list-style: none; }
.proj li { display: flex; gap: 10px; padding-block: 3px; font-size: 13px;
           border-bottom: 1px dashed var(--line); }
.proj li:last-child { border-bottom: 0; }
.proj li .no { color: var(--faint); min-width: 3em; font-variant-numeric: tabular-nums; }
.proj li .what { flex: 1; overflow-wrap: anywhere; }
.proj li .when { color: var(--faint); font-size: 12px; white-space: nowrap;
                 font-variant-numeric: tabular-nums; }

.timeline { background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
            padding: 10px 14px 12px; }
.row { display: flex; align-items: center; gap: 10px; margin: 3px 0; }
.row .h { width: 22px; flex: none; font-size: 11px; color: var(--faint); text-align: right;
          font-family: var(--num); font-variant-numeric: tabular-nums; }
.scale { flex: 1; display: grid; grid-template-columns: repeat(4, 1fr); font-size: 10px;
         color: var(--faint); }
.track { position: relative; flex: 1; height: 22px; background: var(--sunk);
         border-radius: 4px; overflow: hidden; }
.track::after { content: ""; position: absolute; inset: 0; pointer-events: none;
                background: linear-gradient(90deg, transparent 24.9%, var(--line) 25%,
                  transparent 25.15%, transparent 49.9%, var(--line) 50%, transparent 50.15%,
                  transparent 74.9%, var(--line) 75%, transparent 75.15%); opacity: .7; }
.piece { position: absolute; top: 0; bottom: 0; }
.piece .act { position: absolute; left: 0; right: 0; bottom: 0; background: var(--k-act); }
.sleep { position: absolute; top: 0; bottom: 0; z-index: 1;
         background: repeating-linear-gradient(135deg, var(--sleep) 0 2px, transparent 2px 7px);
         opacity: .65; }
.phone, .auto { position: absolute; top: 0; height: 5px; z-index: 2; }
.phone { background: var(--phone); }
.auto { background: repeating-linear-gradient(90deg, var(--auto) 0 4px, transparent 4px 7px); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 11px; color: var(--muted);
          margin-top: 10px; }
.legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px;
            margin-right: 5px; vertical-align: -2px; }
.legend i.swatch-act { background: var(--k-act); }
.legend i.swatch-sleep { background: repeating-linear-gradient(135deg, var(--sleep) 0 2px,
                         transparent 2px 5px); }
.legend i.swatch-phone { background: var(--phone); height: 5px; vertical-align: 2px; }
.legend i.swatch-auto { background: repeating-linear-gradient(90deg, var(--auto) 0 3px,
                        transparent 3px 5px); height: 5px; vertical-align: 2px; }

.table { overflow-x: auto; background: var(--surface); border: 1px solid var(--line);
         border-radius: 12px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 7px 12px; border-bottom: 1px solid var(--line);
         vertical-align: top; }
tr:last-child td { border-bottom: 0; }
th { font-size: 11px; color: var(--muted); font-weight: 600; background: var(--sunk); }
td.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
td.t { overflow-wrap: anywhere; }
details.more > summary { cursor: pointer; color: var(--muted); font-size: 13px;
                         padding-block: 4px; }
.paths { font-size: 12px; color: var(--muted); padding-left: 18px; }

.days { background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
        padding: 6px 14px 10px; margin-top: 16px; }
.dayrow { display: grid; grid-template-columns: 7em 1fr 17em; gap: 12px; align-items: center;
          padding-block: 5px; border-bottom: 1px solid var(--line); }
.dayrow:last-child { border-bottom: 0; }
.dayrow .d { font-weight: 600; white-space: nowrap; font-variant-numeric: tabular-nums; }
.dayrow .d small { color: var(--faint); font-weight: 400; margin-left: 4px; }
.dayrow .track { height: 24px; }
.nums { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; text-align: right;
        font-family: var(--num); font-size: 13px; font-variant-numeric: tabular-nums; }
.dayrow.head { padding-block: 4px; }
.dayrow.head .nums { font-family: var(--font); font-size: 11px; color: var(--muted); }
.hours { position: relative; height: 14px; font-size: 10px; color: var(--faint);
         font-variant-numeric: tabular-nums; }
.hours span { position: absolute; transform: translateX(-50%); }
.hours span:first-child { transform: none; }

@media (max-width: 640px) {
  header.day h1 { font-size: 24px; }
  .stats { grid-template-columns: repeat(2, 1fr); }
  details.proj > summary { grid-template-columns: 12px 1fr auto; }
  details.proj > summary .meter { display: none; }
  details.proj > summary .chips { grid-column: 2 / -1; justify-content: flex-start; }
  .proj .body { padding-left: 0; }
  .dayrow { grid-template-columns: 1fr; gap: 2px; }
  .nums { text-align: left; }
  .dayrow.head .d, .dayrow.head .hours { display: none; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
""")


def e(text):
    return html_escape.escape(str(text if text is not None else ""), quote=True)


def topbar(links):
    """上の帯。`links` は (文字, href または None) の列。None は押せない印として出す。"""
    items = "".join(f"<a href='{e(href)}'>{e(text)}</a>" if href else f"<span>{e(text)}</span>"
                    for text, href in links)
    return (f"<div class='top'><div class='wrap'><a class='brand' href='index.html'>chronofit</a>"
            f"<nav class='nav'>{items}</nav></div></div>")


def page(title, body, top=""):
    return ("<!doctype html>\n<html lang='ja'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            "<meta name='robots' content='noindex,nofollow'>"
            f"<title>{e(title)}</title><style>{CSS}</style></head>"
            f"<body>{top}<main class='wrap'>{body}</main></body></html>\n")
