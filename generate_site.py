#!/usr/bin/env python3
"""
Generador de sitio estático multi-torneo para Netlify.

Lee tournaments_db.json (o tournament_data.json legacy) y genera
dist/index.html con todos los torneos embebidos y un selector.

Uso:
    python generate_site.py                              # usa tournaments_db.json
    python generate_site.py --input tournament_data.json # legacy single-tournament
    python generate_site.py --output public              # otro directorio
"""

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict


DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tournaments_db.json")


def load_data(filepath: str) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Build enriched metagame data from raw tournament JSON ──────────────────

def build_metagame(data: dict) -> dict:
    """Process raw tournament data into metagame analysis."""
    matches = data["matches"]
    matrix_raw = data["matchup_matrix"]["matrix"]
    all_decks = data["matchup_matrix"]["decks"]
    tournament = data["tournament"]

    # ── Player → Deck mapping (last seen deck)
    player_deck = {}
    for m in matches:
        if m.get("player1_deck"):
            player_deck[m["player1"]] = m["player1_deck"]
        if m.get("player2_deck"):
            player_deck[m["player2"]] = m["player2_deck"]

    # ── Deck counts (how many pilots)
    deck_counts = Counter(player_deck.values())
    total_pilots = len(player_deck)

    # ── Per-deck win/loss/draw stats from matches
    deck_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "draws": 0})
    for m in matches:
        d1 = m.get("player1_deck")
        d2 = m.get("player2_deck")
        if not d1 or not d2:
            continue
        p1w = m.get("player1_wins") or 0
        p2w = m.get("player2_wins") or 0
        if p1w > p2w:
            deck_stats[d1]["wins"] += 1
            deck_stats[d2]["losses"] += 1
        elif p2w > p1w:
            deck_stats[d1]["losses"] += 1
            deck_stats[d2]["wins"] += 1
        else:
            deck_stats[d1]["draws"] += 1
            deck_stats[d2]["draws"] += 1

    # ── Build archetype list sorted by count
    archetypes = []
    for deck_name in sorted(deck_counts.keys(), key=lambda d: deck_counts[d], reverse=True):
        st = deck_stats[deck_name]
        total_m = st["wins"] + st["losses"] + st["draws"]
        wr = (st["wins"] / total_m * 100) if total_m > 0 else 0
        share = deck_counts[deck_name] / total_pilots * 100 if total_pilots > 0 else 0
        archetypes.append({
            "name": deck_name,
            "count": deck_counts[deck_name],
            "share": round(share, 2),
            "wins": st["wins"],
            "losses": st["losses"],
            "draws": st["draws"],
            "total": total_m,
            "winrate": round(wr, 2),
        })

    # ── Matchup matrix (only decks with >= 1 match)
    matrix_decks = [a["name"] for a in archetypes if a["total"] > 0]
    matrix = []
    for d1 in matrix_decks:
        row = {}
        for d2 in matrix_decks:
            if d1 == d2:
                row[d2] = {"mirror": True}
            else:
                cell = matrix_raw.get(d1, {}).get(d2, {})
                w = cell.get("wins", 0)
                l = cell.get("losses", 0)
                dr = cell.get("draws", 0)
                t = w + l + dr
                wr = (w / t * 100) if t > 0 else 50
                row[d2] = {"w": w, "l": l, "d": dr, "t": t, "wr": round(wr, 1)}
        matrix.append(row)

    return {
        "tournament": tournament,
        "archetypes": archetypes,
        "matrix_decks": matrix_decks,
        "matrix": matrix,
        "total_pilots": total_pilots,
        "matches_sample": matches[:200],  # sample for detail view
    }


# ── Color palette for pie chart ────────────────────────────────────────────

PIE_COLORS = [
    "#6366f1", "#8b5cf6", "#a78bfa", "#c084fc",  # purples
    "#f472b6", "#fb7185", "#f87171", "#fca5a5",  # pinks/reds
    "#fb923c", "#fbbf24", "#facc15", "#a3e635",  # oranges/yellows
    "#4ade80", "#34d399", "#2dd4bf", "#22d3ee",  # greens/teals
    "#38bdf8", "#60a5fa", "#818cf8", "#a5b4fc",  # blues
    "#e879f9", "#d946ef", "#c026d3", "#a855f7",  # magentas
]


def generate_html(all_metas: dict) -> str:
    """Generate the full HTML dashboard with multi-tournament support.
    
    Args:
        all_metas: dict of {tournament_id: meta_dict} for each tournament
    """

    # Build the TOURNAMENTS JS object with all data
    tournaments_js = {}
    first_id = None
    for tid, meta in all_metas.items():
        if first_id is None:
            first_id = tid
        tournaments_js[tid] = {
            "tournament": meta["tournament"],
            "archetypes": meta["archetypes"],
            "matrix_decks": meta["matrix_decks"],
            "matrix": meta["matrix"],
            "total_pilots": meta["total_pilots"],
        }

    all_tournaments_json = json.dumps(tournaments_js, ensure_ascii=False)
    pie_colors_json = json.dumps(PIE_COLORS)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MTG Meta Analyzer</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎴</text></svg>">
<style>
/* ═══════════════ RESET & BASE ═══════════════ */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg-0:#0b0d14;--bg-1:#111320;--bg-2:#181b2c;--bg-3:#1f2340;
  --border:#2a2f4a;--border-focus:#4f46e5;
  --text-0:#f0f0f5;--text-1:#c5c7d6;--text-2:#8b8fa8;--text-3:#5c6080;
  --accent:#6366f1;--accent-hover:#818cf8;
  --green:#34d399;--yellow:#fbbf24;--red:#f87171;
  --good:rgba(52,211,153,.12);--ok:rgba(251,191,36,.08);--bad:rgba(248,113,113,.12);
  --radius:8px;--shadow:0 4px 24px rgba(0,0,0,.4);
  --transition:all .2s ease;
}}
html{{scroll-behavior:smooth}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg-0);color:var(--text-1);line-height:1.5;min-height:100vh;overflow-x:hidden}}
a{{color:var(--accent);text-decoration:none}}
a:hover{{color:var(--accent-hover)}}
::selection{{background:var(--accent);color:#fff}}
::-webkit-scrollbar{{width:8px;height:8px}}
::-webkit-scrollbar-track{{background:var(--bg-1)}}
::-webkit-scrollbar-thumb{{background:var(--border);border-radius:4px}}
::-webkit-scrollbar-thumb:hover{{background:var(--text-3)}}

/* ═══════════════ LAYOUT ═══════════════ */
.app{{display:flex;flex-direction:column;min-height:100vh}}
.topbar{{background:var(--bg-1);border-bottom:1px solid var(--border);padding:0 1.5rem;display:flex;align-items:center;height:56px;position:sticky;top:0;z-index:100;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}}
.topbar .logo{{font-weight:800;font-size:1.1rem;color:var(--text-0);display:flex;align-items:center;gap:.5rem}}
.topbar .logo span{{background:linear-gradient(135deg,var(--accent),#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.topbar .tournament-name{{color:var(--text-2);font-size:.8rem;margin-left:1rem;padding-left:1rem;border-left:1px solid var(--border);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:500px}}

/* Nav tabs */
.nav{{display:flex;gap:0;margin-left:auto}}
.nav-btn{{background:none;border:none;color:var(--text-2);font-size:.85rem;font-weight:500;padding:.75rem 1.25rem;cursor:pointer;border-bottom:2px solid transparent;transition:var(--transition);white-space:nowrap}}
.nav-btn:hover{{color:var(--text-0);background:var(--bg-2)}}
.nav-btn.active{{color:var(--accent);border-bottom-color:var(--accent)}}
.nav-btn svg{{width:16px;height:16px;vertical-align:-2px;margin-right:6px;fill:currentColor}}

/* Stats ribbon */
.stats-ribbon{{background:var(--bg-1);border-bottom:1px solid var(--border);display:flex;justify-content:center;gap:3rem;padding:.75rem 2rem;flex-wrap:wrap}}
.ribbon-stat{{text-align:center}}
.ribbon-stat .val{{font-size:1.4rem;font-weight:700;color:var(--accent)}}
.ribbon-stat .lbl{{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--text-3)}}

/* Main content */
.main{{flex:1;padding:1.5rem;max-width:1800px;margin:0 auto;width:100%}}
.panel{{display:none;animation:fadeIn .3s ease}}
.panel.active{{display:block}}
@keyframes fadeIn{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:none}}}}

/* Cards */
.card{{background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}}
.card-header{{padding:.75rem 1rem;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap}}
.card-header h2{{font-size:1rem;font-weight:600;color:var(--text-0)}}
.card-body{{padding:1rem}}

/* Filters */
.filters{{display:flex;gap:.75rem;align-items:center;flex-wrap:wrap}}
.filters label{{color:var(--text-2);font-size:.8rem;white-space:nowrap}}
.filter-input{{background:var(--bg-2);border:1px solid var(--border);color:var(--text-1);padding:6px 10px;border-radius:6px;font-size:.82rem;outline:none;transition:var(--transition)}}
.filter-input:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(99,102,241,.15)}}
.filter-input::placeholder{{color:var(--text-3)}}

/* ═══════════════ HOME PANEL ═══════════════ */
.home-hero{{text-align:center;padding:3rem 1.5rem 2rem}}
.home-hero h1{{font-size:2rem;font-weight:800;color:var(--text-0);margin-bottom:.5rem}}
.home-hero h1 span{{background:linear-gradient(135deg,var(--accent),#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.home-hero p{{color:var(--text-2);font-size:.95rem;max-width:500px;margin:0 auto}}

/* Add tournament box */
.add-box{{max-width:640px;margin:2rem auto;background:var(--bg-1);border:2px dashed var(--border);border-radius:12px;padding:2rem;transition:var(--transition)}}
.add-box:focus-within{{border-color:var(--accent);box-shadow:0 0 0 4px rgba(99,102,241,.1)}}
.add-box-label{{display:flex;align-items:center;gap:.5rem;font-size:.9rem;font-weight:600;color:var(--text-0);margin-bottom:1rem}}
.add-box-label svg{{width:20px;height:20px;fill:var(--accent)}}
.add-row{{display:flex;gap:.75rem}}
.add-input{{flex:1;background:var(--bg-2);border:1px solid var(--border);color:var(--text-0);padding:12px 16px;border-radius:8px;font-size:.9rem;outline:none;transition:var(--transition)}}
.add-input:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(99,102,241,.15)}}
.add-input::placeholder{{color:var(--text-3)}}
.add-btn{{background:var(--accent);color:#fff;border:none;padding:12px 24px;border-radius:8px;font-size:.9rem;font-weight:700;cursor:pointer;transition:var(--transition);white-space:nowrap;display:flex;align-items:center;gap:6px}}
.add-btn:hover{{background:var(--accent-hover);transform:translateY(-1px);box-shadow:0 4px 16px rgba(99,102,241,.3)}}
.add-btn:disabled{{opacity:.5;cursor:not-allowed;transform:none;box-shadow:none}}
.add-btn svg{{width:16px;height:16px;fill:currentColor}}
.add-status{{padding:.75rem 1rem;border-radius:8px;font-size:.85rem;margin-top:1rem;display:none}}
.add-status.info{{display:block;background:rgba(99,102,241,.1);color:var(--accent);border:1px solid rgba(99,102,241,.2)}}
.add-status.success{{display:block;background:rgba(52,211,153,.1);color:var(--green);border:1px solid rgba(52,211,153,.2)}}
.add-status.error{{display:block;background:rgba(248,113,113,.1);color:var(--red);border:1px solid rgba(248,113,113,.2)}}
.add-spinner{{display:inline-block;width:16px;height:16px;border:2.5px solid var(--accent);border-top-color:transparent;border-radius:50%;animation:spin .6s linear infinite;vertical-align:-3px;margin-right:6px}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}

/* Progress bar */
.progress-wrap{{max-width:640px;margin:1.5rem auto 0;display:none}}
.progress-wrap.active{{display:block}}
.progress-label{{display:flex;justify-content:space-between;font-size:.8rem;color:var(--text-2);margin-bottom:6px}}
.progress-track{{height:10px;background:var(--bg-2);border-radius:99px;overflow:hidden;position:relative}}
.progress-fill{{height:100%;border-radius:99px;background:linear-gradient(90deg,var(--accent),#818cf8);width:0%;transition:width .4s ease;position:relative}}
.progress-fill.indeterminate{{width:30%!important;animation:indeterminate 1.5s ease-in-out infinite}}
@keyframes indeterminate{{0%{{transform:translateX(-100%);width:30%}}50%{{width:45%}}100%{{transform:translateX(400%);width:30%}}}}
.progress-steps{{display:flex;gap:.5rem;margin-top:.75rem;flex-wrap:wrap}}
.progress-step{{font-size:.75rem;padding:4px 10px;border-radius:6px;background:var(--bg-2);color:var(--text-3);transition:all .3s}}
.progress-step.active{{background:rgba(99,102,241,.15);color:var(--accent)}}
.progress-step.done{{background:rgba(52,211,153,.15);color:var(--green)}}
.progress-step.done::before{{content:'✓ '}}
.progress-step.error{{background:rgba(248,113,113,.15);color:var(--red)}}
.progress-step.error::before{{content:'✕ '}}

/* Tournament cards grid */
.t-grid-title{{max-width:640px;margin:2.5rem auto .75rem;font-size:.85rem;font-weight:600;color:var(--text-2);text-transform:uppercase;letter-spacing:.06em;padding:0 .25rem}}
.t-grid{{max-width:640px;margin:0 auto;display:flex;flex-direction:column;gap:.5rem;padding-bottom:3rem}}
.t-card{{background:var(--bg-1);border:1px solid var(--border);border-radius:10px;padding:1rem 1.25rem;display:flex;align-items:center;gap:1rem;cursor:pointer;transition:var(--transition)}}
.t-card:hover{{border-color:var(--accent);background:var(--bg-2);transform:translateY(-1px);box-shadow:0 4px 20px rgba(0,0,0,.3)}}
.t-card-icon{{font-size:1.5rem;flex-shrink:0}}
.t-card-info{{flex:1;min-width:0}}
.t-card-name{{font-weight:700;color:var(--text-0);font-size:.92rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.t-card-stats{{display:flex;gap:1rem;margin-top:4px;font-size:.75rem;color:var(--text-3)}}
.t-card-stats span{{display:flex;align-items:center;gap:3px}}
.t-card-actions{{display:flex;gap:.5rem;flex-shrink:0}}
.t-card-go{{background:var(--accent);color:#fff;border:none;padding:6px 16px;border-radius:6px;font-size:.8rem;font-weight:600;cursor:pointer;transition:var(--transition)}}
.t-card-go:hover{{background:var(--accent-hover)}}
.t-card-del{{background:none;border:1px solid var(--border);color:var(--text-3);width:30px;height:30px;border-radius:6px;cursor:pointer;font-size:1rem;transition:var(--transition);display:flex;align-items:center;justify-content:center}}
.t-card-del:hover{{border-color:var(--red);color:var(--red);background:rgba(248,113,113,.08)}}
.t-empty{{text-align:center;padding:2rem;color:var(--text-3);font-size:.9rem}}

/* Static mode: hide server-only features */
.static-mode .add-box{{display:none}}
.static-mode .t-card-del{{display:none}}

/* Active tournament bar (shown in analysis views) */
.active-bar{{background:var(--bg-2);border-bottom:1px solid var(--border);padding:.5rem 1.5rem;display:flex;align-items:center;gap:1rem;flex-wrap:wrap;display:none}}
.active-bar.show{{display:flex}}
.active-bar-name{{color:var(--text-0);font-weight:600;font-size:.85rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}}
.active-bar-back{{background:none;border:1px solid var(--border);color:var(--text-2);padding:5px 12px;border-radius:6px;font-size:.78rem;cursor:pointer;transition:var(--transition);display:flex;align-items:center;gap:4px}}
.active-bar-back:hover{{border-color:var(--accent);color:var(--accent)}}
.active-bar-back svg{{width:12px;height:12px;fill:currentColor}}

/* ═══════════════ METAGAME PAGE ═══════════════ */
.meta-grid{{display:grid;grid-template-columns:320px 1fr;gap:1.5rem;margin-top:1.5rem}}
@media(max-width:1024px){{.meta-grid{{grid-template-columns:1fr}}}}

/* Pie chart */
.pie-container{{position:relative;width:280px;height:280px;margin:1rem auto}}
.pie-container svg{{width:100%;height:100%}}
.pie-center{{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center}}
.pie-center .big{{font-size:2rem;font-weight:800;color:var(--text-0)}}
.pie-center .sub{{font-size:.75rem;color:var(--text-3)}}
.pie-legend{{margin-top:1rem;max-height:300px;overflow-y:auto;padding:0 .5rem}}
.pie-legend-item{{display:flex;align-items:center;gap:.5rem;padding:4px 0;font-size:.78rem;cursor:pointer;border-radius:4px;transition:var(--transition)}}
.pie-legend-item:hover{{background:var(--bg-2);padding-left:4px}}
.pie-legend-item .dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.pie-legend-item .name{{flex:1;color:var(--text-1);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.pie-legend-item .pct{{color:var(--text-2);font-weight:600;font-variant-numeric:tabular-nums}}

/* Meta table */
.meta-table{{width:100%;border-collapse:separate;border-spacing:0;font-size:.82rem}}
.meta-table thead th{{position:sticky;top:0;background:var(--bg-1);color:var(--text-2);font-weight:600;text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);cursor:pointer;user-select:none;white-space:nowrap;transition:var(--transition);z-index:2}}
.meta-table thead th:hover{{color:var(--accent)}}
.meta-table thead th.sorted-asc::after{{content:' ▲';font-size:.65rem}}
.meta-table thead th.sorted-desc::after{{content:' ▼';font-size:.65rem}}
.meta-table tbody td{{padding:7px 10px;border-bottom:1px solid var(--bg-2)}}
.meta-table tbody tr{{transition:var(--transition)}}
.meta-table tbody tr:hover{{background:var(--bg-2)}}
.meta-table .name-cell{{font-weight:600;color:var(--text-0);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.meta-table .num{{font-variant-numeric:tabular-nums;text-align:right}}
.share-bar-bg{{display:inline-flex;align-items:center;gap:6px;width:100%}}
.share-bar{{height:6px;border-radius:3px;transition:width .4s ease}}
.wr-high{{color:var(--green)}}
.wr-mid{{color:var(--yellow)}}
.wr-low{{color:var(--red)}}
.wr-badge{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:.72rem;font-weight:700}}
.wr-badge.high{{background:rgba(52,211,153,.15);color:var(--green)}}
.wr-badge.mid{{background:rgba(251,191,36,.12);color:var(--yellow)}}
.wr-badge.low{{background:rgba(248,113,113,.15);color:var(--red)}}

/* ═══════════════ MATCHUP MATRIX ═══════════════ */
.matrix-wrap{{overflow:auto;max-height:calc(100vh - 240px);border-radius:var(--radius)}}
.mx{{border-collapse:separate;border-spacing:0;font-size:.72rem}}
.mx th,.mx td{{padding:3px 4px;text-align:center;border:1px solid var(--bg-0);min-width:58px;max-width:80px;transition:background .15s}}
.mx thead th{{background:var(--bg-2);color:var(--text-2);font-weight:600;position:sticky;top:0;z-index:3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.mx thead th:first-child{{left:0;z-index:4}}
.mx .rh{{position:sticky;left:0;z-index:2;background:var(--bg-1);text-align:left;font-weight:600;color:var(--text-0);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:130px;max-width:160px;padding-left:8px}}
.mx .mirror{{background:var(--bg-2);color:var(--text-3)}}
.mx .cell-good{{background:var(--good);color:var(--green);font-weight:600}}
.mx .cell-ok{{background:var(--ok);color:var(--yellow)}}
.mx .cell-bad{{background:var(--bad);color:var(--red)}}
.mx .cell-nd{{color:var(--text-3);font-size:.65rem}}
.mx td:not(.rh):not(.mirror){{cursor:default}}
.mx td:not(.rh):hover{{outline:2px solid var(--accent);outline-offset:-1px;z-index:5}}
.mx tr.row-hl td{{background-color:rgba(99,102,241,.06)!important}}
.mx th.col-hl{{background-color:rgba(99,102,241,.15)!important}}

/* Matrix detail tooltip */
.tt{{position:fixed;background:var(--bg-2);border:1px solid var(--border);border-radius:8px;padding:10px 14px;font-size:.8rem;pointer-events:none;z-index:1000;box-shadow:var(--shadow);min-width:180px;transition:opacity .15s;opacity:0}}
.tt.show{{opacity:1}}
.tt .tt-h{{font-weight:700;color:var(--text-0);margin-bottom:4px;font-size:.85rem}}
.tt .tt-sub{{color:var(--text-2);font-size:.75rem;margin-bottom:6px}}
.tt .tt-wr{{font-size:1.3rem;font-weight:800;margin:4px 0}}
.tt .tt-rec{{color:var(--text-2);font-size:.78rem}}
.tt .tt-bar{{height:4px;border-radius:2px;margin-top:6px;overflow:hidden;display:flex}}
.tt .tt-bar-w{{background:var(--green)}}
.tt .tt-bar-l{{background:var(--red)}}
.tt .tt-bar-d{{background:var(--yellow)}}

/* ═══════════════ DECK DETAIL PANEL ═══════════════ */
.deck-detail-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:1rem;margin-top:1rem}}
.matchup-bar{{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:.8rem}}
.matchup-bar .bar-bg{{flex:1;height:8px;background:var(--bg-2);border-radius:4px;overflow:hidden;min-width:80px}}
.matchup-bar .bar-fill{{height:100%;border-radius:4px;transition:width .4s ease}}
.matchup-bar .bar-fill.good{{background:var(--green)}}
.matchup-bar .bar-fill.ok{{background:var(--yellow)}}
.matchup-bar .bar-fill.bad{{background:var(--red)}}
.matchup-bar .opp{{width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-1)}}
.matchup-bar .wr-val{{width:45px;text-align:right;font-weight:600;font-variant-numeric:tabular-nums}}
.matchup-bar .rec{{color:var(--text-3);font-size:.72rem;width:60px;text-align:right}}

/* ═══════════════ RESPONSIVE ═══════════════ */
@media(max-width:768px){{
  .topbar{{padding:0 .75rem}}
  .topbar .tournament-name{{display:none}}
  .nav-btn{{padding:.6rem .75rem;font-size:.8rem}}
  .stats-ribbon{{gap:1.5rem;padding:.5rem 1rem}}
  .main{{padding:.75rem}}
  .meta-grid{{grid-template-columns:1fr}}
}}

/* ═══════════════ PRINT ═══════════════ */
@media print{{
  .topbar,.stats-ribbon,.filters,.nav{{display:none!important}}
  .panel{{display:block!important}}
  body{{background:#fff;color:#000}}
  .card{{border:1px solid #ccc}}
}}
</style>
</head>
<body>
<div class="app">

<!-- ═══ TOP BAR ═══ -->
<header class="topbar">
  <div class="logo">🎴 <span>MTG Meta Analyzer</span></div>
  <nav class="nav">
    <button class="nav-btn active" data-panel="home-panel" id="nav-home">
      <svg viewBox="0 0 24 24" style="fill:none;stroke:currentColor;stroke-width:2"><path d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-4 0h4"/></svg>
      Torneos
    </button>
    <button class="nav-btn" data-panel="meta-panel" id="nav-meta">
      <svg viewBox="0 0 24 24"><path d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-4 0h4"/></svg>
      Metagame
    </button>
    <button class="nav-btn" data-panel="matrix-panel" id="nav-matrix">
      <svg viewBox="0 0 24 24"><path d="M4 6h16M4 10h16M4 14h16M4 18h16"/></svg>
      Matchups
    </button>
    <button class="nav-btn" data-panel="detail-panel" id="nav-detail">
      <svg viewBox="0 0 24 24"><path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/></svg>
      Deck Detail
    </button>
  </nav>
</header>

<!-- ═══ ACTIVE TOURNAMENT BAR (shown in analysis views) ═══ -->
<div class="active-bar" id="active-bar">
  <button class="active-bar-back" id="btn-back-home">
    <svg viewBox="0 0 24 24"><path d="M15 19l-7-7 7-7"/></svg>
    Torneos
  </button>
  <div class="active-bar-name" id="active-bar-name">—</div>
</div>

<!-- ═══ STATS RIBBON ═══ -->
<div class="stats-ribbon">
  <div class="ribbon-stat"><div class="val" id="r-players">-</div><div class="lbl">Players</div></div>
  <div class="ribbon-stat"><div class="val" id="r-matches">-</div><div class="lbl">Matches</div></div>
  <div class="ribbon-stat"><div class="val" id="r-decks">-</div><div class="lbl">Archetypes</div></div>
  <div class="ribbon-stat"><div class="val" id="r-rounds">-</div><div class="lbl">Rounds</div></div>
</div>

<!-- ═══ CONTENT ═══ -->
<div class="main">

  <!-- ──── HOME PANEL ──── -->
  <div id="home-panel" class="panel active">
    <div class="home-hero">
      <h1>🎴 <span>MTG Meta Analyzer</span></h1>
      <p>Selecciona un torneo para ver su metagame, matrix de matchups y detalle de mazos.</p>
    </div>

    <!-- Add tournament box (hidden in static mode) -->
    <div class="add-box" id="add-box">
      <div class="add-box-label">
        <svg viewBox="0 0 24 24"><path d="M12 4v16m8-8H4" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round"/></svg>
        Añadir nuevo torneo
      </div>
      <div class="add-row">
        <input class="add-input" id="add-url" type="text" placeholder="Pega la URL: https://melee.gg/Tournament/View/...">
        <button class="add-btn" id="btn-do-add">
          <svg viewBox="0 0 24 24"><path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>
          Scrapear
        </button>
      </div>
      <div class="add-status" id="add-status"></div>
    </div>

    <!-- Progress bar -->
    <div class="progress-wrap" id="progress-wrap">
      <div class="progress-label">
        <span id="progress-text">Preparando...</span>
        <span id="progress-pct">0%</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill" id="progress-fill"></div>
      </div>
      <div class="progress-steps" id="progress-steps">
        <span class="progress-step" data-step="validate">Validar URL</span>
        <span class="progress-step" data-step="connect">Conectar melee.gg</span>
        <span class="progress-step" data-step="rounds">Descargar rondas</span>
        <span class="progress-step" data-step="matches">Procesar partidas</span>
        <span class="progress-step" data-step="matrix">Construir matrix</span>
        <span class="progress-step" data-step="save">Guardar datos</span>
      </div>
    </div>

    <!-- Tournament list -->
    <div class="t-grid-title" id="t-grid-title">Torneos disponibles</div>
    <div class="t-grid" id="t-grid"></div>
  </div>

  <!-- ──── METAGAME PANEL ──── -->
  <div id="meta-panel" class="panel">
    <div class="card">
      <div class="card-header">
        <h2>Metagame Breakdown</h2>
        <div class="filters">
          <label>Min matches</label>
          <input class="filter-input" type="number" id="f-min" value="5" min="0" max="999" style="width:65px">
          <label>Search</label>
          <input class="filter-input" type="text" id="f-search" placeholder="Filter archetypes…" style="width:180px">
        </div>
      </div>
      <div class="card-body">
        <div class="meta-grid">
          <!-- Pie Chart -->
          <div>
            <div class="pie-container">
              <svg id="pie-svg" viewBox="0 0 200 200"></svg>
              <div class="pie-center">
                <div class="big" id="pie-total">-</div>
                <div class="sub">archetypes</div>
              </div>
            </div>
            <div class="pie-legend" id="pie-legend"></div>
          </div>
          <!-- Table -->
          <div style="overflow-x:auto">
            <table class="meta-table" id="meta-tbl">
              <thead>
                <tr>
                  <th data-key="idx" style="width:40px">#</th>
                  <th data-key="name">Archetype</th>
                  <th data-key="share">Meta&nbsp;%</th>
                  <th data-key="count" class="num">Pilots</th>
                  <th data-key="wins" class="num">W</th>
                  <th data-key="losses" class="num">L</th>
                  <th data-key="draws" class="num">D</th>
                  <th data-key="total" class="num">Matches</th>
                  <th data-key="winrate" class="num">Win&nbsp;%</th>
                </tr>
              </thead>
              <tbody id="meta-body"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ──── MATCHUP MATRIX PANEL ──── -->
  <div id="matrix-panel" class="panel">
    <div class="card">
      <div class="card-header">
        <h2>Matchup Matrix</h2>
        <div class="filters">
          <label>Min sample</label>
          <input class="filter-input" type="number" id="mx-min" value="5" min="1" max="200" style="width:65px">
          <label>Top N decks</label>
          <input class="filter-input" type="number" id="mx-top" value="25" min="3" max="200" style="width:65px">
          <label>Search</label>
          <input class="filter-input" type="text" id="mx-search" placeholder="Filter…" style="width:150px">
        </div>
      </div>
      <div class="card-body" style="padding:0">
        <div class="matrix-wrap" id="mx-wrap">
          <table class="mx" id="mx-tbl"></table>
        </div>
      </div>
    </div>
  </div>

  <!-- ──── DECK DETAIL PANEL ──── -->
  <div id="detail-panel" class="panel">
    <div class="card">
      <div class="card-header">
        <h2>Deck Detail</h2>
        <div class="filters">
          <label>Select archetype</label>
          <select class="filter-input" id="dd-select" style="width:250px"></select>
          <label>Min sample</label>
          <input class="filter-input" type="number" id="dd-min" value="3" min="1" max="100" style="width:65px">
        </div>
      </div>
      <div class="card-body">
        <div id="dd-overview" style="margin-bottom:1.5rem"></div>
        <h3 style="font-size:.95rem;color:var(--text-0);margin-bottom:.75rem">Matchup Breakdown</h3>
        <div id="dd-matchups"></div>
      </div>
    </div>
  </div>

</div><!-- /main -->
</div><!-- /app -->

<!-- ═══ TOOLTIP ═══ -->
<div class="tt" id="tt"></div>

<script>
/* ══════════════════════════════════════════════════
   DATA (embedded at build time — ALL tournaments)
   ══════════════════════════════════════════════════ */
const ALL_TOURNAMENTS = {all_tournaments_json};
const PIE_COLORS = {pie_colors_json};

/* Detect static mode (no API server) */
let IS_STATIC = true;
fetch('/api/status').then(r => r.json()).then(d => {{
  if (d.ok) {{ IS_STATIC = false; document.body.classList.remove('static-mode'); }}
}}).catch(() => {{}});
document.body.classList.add('static-mode');

/* Current tournament state */
let currentTournamentId = null;
let ARCHETYPES = [];
let MX_DECKS   = [];
let MX_DATA    = [];

function loadTournament(tid) {{
  const t = ALL_TOURNAMENTS[tid];
  if (!t) return;
  currentTournamentId = tid;
  ARCHETYPES = t.archetypes;
  MX_DECKS   = t.matrix_decks;
  MX_DATA    = t.matrix;

  // Update stats ribbon
  const info = t.tournament;
  const activeDecks = t.archetypes.filter(a => a.total > 0).length;
  document.getElementById('r-players').textContent = t.total_pilots;
  document.getElementById('r-matches').textContent = info.total_matches;
  document.getElementById('r-decks').textContent = activeDecks;
  document.getElementById('r-rounds').textContent = info.total_rounds;
  document.getElementById('active-bar-name').textContent = '🏆 ' + info.name;
  document.title = 'MTG Meta Analyzer — ' + info.name;

  // Re-render active panel
  populateSelect();
  renderMetaTable();
  const matrixPanel = document.getElementById('matrix-panel');
  if (matrixPanel.classList.contains('active')) renderMatrix();
  const detailPanel = document.getElementById('detail-panel');
  if (detailPanel.classList.contains('active')) renderDetail();
}}

/* Navigate to tournament analysis */
function openTournamentAnalysis(tid) {{
  loadTournament(tid);
  // Switch to metagame panel
  switchPanel('meta-panel');
}}

/* ══════════════════════════════════════════════════
   NAVIGATION
   ══════════════════════════════════════════════════ */
function switchPanel(panelId) {{
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  const btn = document.querySelector(`.nav-btn[data-panel="${{panelId}}"]`);
  if (btn) btn.classList.add('active');
  document.getElementById(panelId).classList.add('active');

  // Show/hide active bar and stats ribbon
  const isHome = panelId === 'home-panel';
  document.getElementById('active-bar').classList.toggle('show', !isHome);
  document.querySelector('.stats-ribbon').style.display = isHome ? 'none' : 'flex';

  if (panelId === 'matrix-panel') renderMatrix();
  if (panelId === 'detail-panel') renderDetail();
}}

document.querySelectorAll('.nav-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    const panel = btn.dataset.panel;
    // If going to analysis without a tournament, ignore
    if (panel !== 'home-panel' && !currentTournamentId) return;
    switchPanel(panel);
  }});
}});

// Back to home button
document.getElementById('btn-back-home').addEventListener('click', () => switchPanel('home-panel'));

// Hide stats ribbon initially
document.querySelector('.stats-ribbon').style.display = 'none';

/* ══════════════════════════════════════════════════
   UTILITY
   ══════════════════════════════════════════════════ */
function wrCls(wr) {{ return wr >= 55 ? 'high' : wr >= 45 ? 'mid' : 'low'; }}
function wrColor(wr) {{ return wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--yellow)' : 'var(--red)'; }}
function esc(s) {{ const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }}
function clamp(v, lo, hi) {{ return Math.max(lo, Math.min(hi, v)); }}

/* ══════════════════════════════════════════════════
   PIE CHART (SVG donut)
   ══════════════════════════════════════════════════ */
function renderPie(data) {{
  const svg = document.getElementById('pie-svg');
  const legend = document.getElementById('pie-legend');
  const cx = 100, cy = 100, r = 80, inner = 55;
  let total = data.reduce((s, a) => s + a.count, 0);
  if (total === 0) {{ svg.innerHTML = ''; legend.innerHTML = ''; return; }}

  let html = '';
  let legendHtml = '';
  let angle = -90;

  // Top 15 slices + "Other"
  let slices = data.slice(0, 15);
  const rest = data.slice(15);
  if (rest.length > 0) {{
    slices.push({{
      name: `Other (${{rest.length}})`,
      count: rest.reduce((s, a) => s + a.count, 0),
      share: rest.reduce((s, a) => s + a.share, 0),
    }});
  }}

  slices.forEach((a, i) => {{
    const pct = a.count / total;
    const sweep = pct * 360;
    const large = sweep > 180 ? 1 : 0;
    const rad1 = (angle * Math.PI) / 180;
    const rad2 = ((angle + sweep) * Math.PI) / 180;

    const x1o = cx + r * Math.cos(rad1);
    const y1o = cy + r * Math.sin(rad1);
    const x2o = cx + r * Math.cos(rad2);
    const y2o = cy + r * Math.sin(rad2);
    const x1i = cx + inner * Math.cos(rad2);
    const y1i = cy + inner * Math.sin(rad2);
    const x2i = cx + inner * Math.cos(rad1);
    const y2i = cy + inner * Math.sin(rad1);

    const color = PIE_COLORS[i % PIE_COLORS.length];
    html += `<path d="M${{x1o}},${{y1o}} A${{r}},${{r}} 0 ${{large}} 1 ${{x2o}},${{y2o}} L${{x1i}},${{y1i}} A${{inner}},${{inner}} 0 ${{large}} 0 ${{x2i}},${{y2i}} Z" fill="${{color}}" opacity="0.85" stroke="var(--bg-0)" stroke-width="1.5">
      <title>${{esc(a.name)}}: ${{a.count}} (${{(pct*100).toFixed(1)}}%)</title>
    </path>`;

    legendHtml += `<div class="pie-legend-item" data-idx="${{i}}">
      <div class="dot" style="background:${{color}}"></div>
      <div class="name">${{esc(a.name)}}</div>
      <div class="pct">${{(pct*100).toFixed(1)}}%</div>
    </div>`;
    angle += sweep;
  }});

  svg.innerHTML = html;
  legend.innerHTML = legendHtml;
}}

/* ══════════════════════════════════════════════════
   METAGAME TABLE
   ══════════════════════════════════════════════════ */
let metaSortKey = 'share';
let metaSortDir = -1; // -1 = desc

function renderMetaTable() {{
  const minM = parseInt(document.getElementById('f-min').value) || 0;
  const search = document.getElementById('f-search').value.toLowerCase();

  let filtered = ARCHETYPES.filter(a => a.total >= minM && a.name.toLowerCase().includes(search));
  filtered.sort((a, b) => {{
    let va = a[metaSortKey], vb = b[metaSortKey];
    if (typeof va === 'string') return metaSortDir * va.localeCompare(vb);
    return metaSortDir * (va - vb);
  }});

  const maxShare = Math.max(...filtered.map(a => a.share), 0.1);
  const tbody = document.getElementById('meta-body');

  tbody.innerHTML = filtered.map((a, i) => {{
    const cls = wrCls(a.winrate);
    const barW = Math.max(2, (a.share / maxShare) * 120);
    return `<tr data-deck="${{esc(a.name)}}" style="cursor:pointer">
      <td class="num">${{i + 1}}</td>
      <td class="name-cell">${{esc(a.name)}}</td>
      <td><div class="share-bar-bg"><span class="share-bar" style="width:${{barW}}px;background:${{PIE_COLORS[i % PIE_COLORS.length]}}"></span><span class="num">${{a.share.toFixed(1)}}%</span></div></td>
      <td class="num">${{a.count}}</td>
      <td class="num">${{a.wins}}</td>
      <td class="num">${{a.losses}}</td>
      <td class="num">${{a.draws}}</td>
      <td class="num">${{a.total}}</td>
      <td class="num"><span class="wr-badge ${{cls}}">${{a.winrate.toFixed(1)}}%</span></td>
    </tr>`;
  }}).join('');

  // Click row → go to deck detail
  tbody.querySelectorAll('tr[data-deck]').forEach(tr => {{
    tr.addEventListener('click', () => {{
      const name = tr.dataset.deck;
      document.getElementById('dd-select').value = name;
      document.querySelector('.nav-btn[data-panel="detail-panel"]').click();
    }});
  }});

  renderPie(filtered);
  document.getElementById('pie-total').textContent = filtered.length;

  // Update header sort indicators
  document.querySelectorAll('#meta-tbl thead th[data-key]').forEach(th => {{
    th.classList.remove('sorted-asc', 'sorted-desc');
    if (th.dataset.key === metaSortKey) {{
      th.classList.add(metaSortDir > 0 ? 'sorted-asc' : 'sorted-desc');
    }}
  }});
}}

// Sort on header click
document.querySelectorAll('#meta-tbl thead th[data-key]').forEach(th => {{
  th.addEventListener('click', () => {{
    const key = th.dataset.key;
    if (key === 'idx') return;
    if (metaSortKey === key) metaSortDir *= -1;
    else {{ metaSortKey = key; metaSortDir = key === 'name' ? 1 : -1; }}
    renderMetaTable();
  }});
}});

document.getElementById('f-min').addEventListener('input', renderMetaTable);
document.getElementById('f-search').addEventListener('input', renderMetaTable);

/* Render home panel tournament list */
function renderHomeTournaments() {{
  const grid = document.getElementById('t-grid');
  const tids = Object.keys(ALL_TOURNAMENTS);
  
  if (tids.length === 0) {{
    grid.innerHTML = '<div class="t-empty">No hay torneos cargados todavía.<br>Usa el formulario de arriba para añadir uno.</div>';
    document.getElementById('t-grid-title').textContent = '';
    return;
  }}

  document.getElementById('t-grid-title').textContent = tids.length + ' torneo' + (tids.length !== 1 ? 's' : '') + ' disponible' + (tids.length !== 1 ? 's' : '');

  grid.innerHTML = tids.map(tid => {{
    const t = ALL_TOURNAMENTS[tid].tournament;
    const pilots = ALL_TOURNAMENTS[tid].total_pilots;
    return `<div class="t-card" data-tid="${{tid}}">
      <div class="t-card-icon">🏆</div>
      <div class="t-card-info">
        <div class="t-card-name">${{esc(t.name)}}</div>
        <div class="t-card-stats">
          <span>👥 ${{pilots}} players</span>
          <span>⚔️ ${{t.total_matches}} matches</span>
          <span>🎲 ${{t.total_rounds}} rounds</span>
        </div>
      </div>
      <div class="t-card-actions">
        <button class="t-card-go" data-tid="${{tid}}">Analizar →</button>
        <button class="t-card-del" data-tid="${{tid}}" title="Eliminar">✕</button>
      </div>
    </div>`;
  }}).join('');

  // Click card or "Analizar" button → open analysis
  grid.querySelectorAll('.t-card-go').forEach(btn => {{
    btn.addEventListener('click', e => {{
      e.stopPropagation();
      openTournamentAnalysis(btn.dataset.tid);
    }});
  }});
  grid.querySelectorAll('.t-card').forEach(card => {{
    card.addEventListener('click', () => openTournamentAnalysis(card.dataset.tid));
  }});

  // Delete button
  grid.querySelectorAll('.t-card-del').forEach(btn => {{
    btn.addEventListener('click', async e => {{
      e.stopPropagation();
      const tid = btn.dataset.tid;
      const name = ALL_TOURNAMENTS[tid]?.tournament?.name || tid;
      if (!confirm(`¿Eliminar "${{name}}"?`)) return;
      try {{
        const res = await fetch(`/api/tournaments/${{tid}}`, {{ method: 'DELETE' }});
        const data = await res.json();
        if (data.ok) {{
          setTimeout(() => window.location.reload(), 400);
        }} else {{
          alert('Error: ' + data.error);
        }}
      }} catch (err) {{
        // If running as static file, remove from local state
        delete ALL_TOURNAMENTS[tid];
        renderHomeTournaments();
      }}
    }});
  }});
}}

/* Initial render of home panel */
renderHomeTournaments();

/* ══════════════════════════════════════════════════
   MATCHUP MATRIX
   ══════════════════════════════════════════════════ */
function renderMatrix() {{
  const minS = parseInt(document.getElementById('mx-min').value) || 1;
  const topN = parseInt(document.getElementById('mx-top').value) || 25;
  const search = document.getElementById('mx-search').value.toLowerCase();

  // Pick top N decks by pilot count, optionally filtered
  let candidates = ARCHETYPES.filter(a => a.total > 0);
  if (search) candidates = candidates.filter(a => a.name.toLowerCase().includes(search));
  candidates = candidates.slice(0, topN);
  const names = candidates.map(a => a.name);

  // Build index map for matrix data
  const idxMap = {{}};
  MX_DECKS.forEach((n, i) => idxMap[n] = i);

  const tbl = document.getElementById('mx-tbl');
  let h = '<thead><tr><th class="rh" style="min-width:130px">Archetype</th>';
  names.forEach((n, j) => {{
    h += `<th data-col="${{j}}" title="${{esc(n)}}">${{n.length > 12 ? esc(n.slice(0,11)) + '…' : esc(n)}}</th>`;
  }});
  h += '</tr></thead><tbody>';

  names.forEach((n1, i) => {{
    const ri = idxMap[n1];
    h += `<tr data-row="${{i}}"><td class="rh" title="${{esc(n1)}}">${{esc(n1.length > 20 ? n1.slice(0,19)+'…' : n1)}}</td>`;
    names.forEach((n2, j) => {{
      if (n1 === n2) {{
        h += '<td class="mirror">—</td>';
      }} else if (ri === undefined) {{
        h += '<td class="cell-nd">—</td>';
      }} else {{
        const cell = MX_DATA[ri] ? MX_DATA[ri][n2] : null;
        if (!cell || cell.mirror || cell.t < minS) {{
          h += '<td class="cell-nd">—</td>';
        }} else {{
          const cls = cell.wr >= 55 ? 'cell-good' : cell.wr >= 45 ? 'cell-ok' : 'cell-bad';
          h += `<td class="${{cls}}" data-a1="${{esc(n1)}}" data-a2="${{esc(n2)}}" data-w="${{cell.w}}" data-l="${{cell.l}}" data-d="${{cell.d}}" data-t="${{cell.t}}" data-wr="${{cell.wr}}">${{cell.wr}}%<br><span style="font-size:.6rem;opacity:.6">${{cell.w}}-${{cell.l}}-${{cell.d}}</span></td>`;
        }}
      }}
    }});
    h += '</tr>';
  }});
  h += '</tbody>';
  tbl.innerHTML = h;

  // ── Row/Column highlight
  const rows = tbl.querySelectorAll('tbody tr');
  const colThs = tbl.querySelectorAll('thead th');
  tbl.addEventListener('mouseover', e => {{
    const td = e.target.closest('td');
    if (!td || td.classList.contains('rh')) return;
    const tr = td.closest('tr');
    const ri = tr ? parseInt(tr.dataset.row) : -1;
    const ci = Array.from(tr.children).indexOf(td);
    rows.forEach(r => r.classList.remove('row-hl'));
    colThs.forEach(th => th.classList.remove('col-hl'));
    if (tr) tr.classList.add('row-hl');
    if (ci >= 0 && colThs[ci]) colThs[ci].classList.add('col-hl');
  }});
  tbl.addEventListener('mouseleave', () => {{
    rows.forEach(r => r.classList.remove('row-hl'));
    colThs.forEach(th => th.classList.remove('col-hl'));
  }});

  // ── Tooltips
  const tt = document.getElementById('tt');
  tbl.querySelectorAll('td[data-a1]').forEach(td => {{
    td.addEventListener('mouseenter', e => {{
      const w = +td.dataset.w, l = +td.dataset.l, d = +td.dataset.d, t = +td.dataset.t, wr = +td.dataset.wr;
      const wPct = t > 0 ? (w/t*100) : 0;
      const lPct = t > 0 ? (l/t*100) : 0;
      const dPct = t > 0 ? (d/t*100) : 0;
      tt.innerHTML = `
        <div class="tt-h">${{td.dataset.a1}}</div>
        <div class="tt-sub">vs ${{td.dataset.a2}}</div>
        <div class="tt-wr" style="color:${{wrColor(wr)}}">${{wr}}%</div>
        <div class="tt-rec">${{w}}W – ${{l}}L – ${{d}}D &nbsp;(${{t}} matches)</div>
        <div class="tt-bar">
          <div class="tt-bar-w" style="width:${{wPct}}%"></div>
          <div class="tt-bar-d" style="width:${{dPct}}%"></div>
          <div class="tt-bar-l" style="width:${{lPct}}%"></div>
        </div>`;
      tt.classList.add('show');
      positionTooltip(e);
    }});
    td.addEventListener('mousemove', positionTooltip);
    td.addEventListener('mouseleave', () => tt.classList.remove('show'));
  }});

  // Click cell → detail
  tbl.querySelectorAll('td[data-a1]').forEach(td => {{
    td.addEventListener('dblclick', () => {{
      document.getElementById('dd-select').value = td.dataset.a1;
      document.querySelector('.nav-btn[data-panel="detail-panel"]').click();
    }});
  }});
}}

function positionTooltip(e) {{
  const tt = document.getElementById('tt');
  const x = e.clientX, y = e.clientY;
  const pad = 14;
  let left = x + pad, top = y + pad;
  if (left + 200 > window.innerWidth) left = x - 200 - pad;
  if (top + 150 > window.innerHeight) top = y - 150 - pad;
  tt.style.left = left + 'px';
  tt.style.top = top + 'px';
}}

document.getElementById('mx-min').addEventListener('input', renderMatrix);
document.getElementById('mx-top').addEventListener('input', renderMatrix);
document.getElementById('mx-search').addEventListener('input', renderMatrix);

/* ══════════════════════════════════════════════════
   DECK DETAIL
   ══════════════════════════════════════════════════ */
function populateSelect() {{
  const sel = document.getElementById('dd-select');
  sel.innerHTML = ARCHETYPES.filter(a => a.total > 0).map(a =>
    `<option value="${{esc(a.name)}}">${{esc(a.name)}} (${{a.share.toFixed(1)}}%)</option>`
  ).join('');
}}

function renderDetail() {{
  const name = document.getElementById('dd-select').value;
  const minS = parseInt(document.getElementById('dd-min').value) || 1;
  if (!name) return;

  const arch = ARCHETYPES.find(a => a.name === name);
  if (!arch) return;

  // Overview
  const overview = document.getElementById('dd-overview');
  const cls = wrCls(arch.winrate);
  overview.innerHTML = `
    <div style="display:flex;gap:2rem;align-items:center;flex-wrap:wrap">
      <div>
        <div style="font-size:1.5rem;font-weight:800;color:var(--text-0)">${{esc(arch.name)}}</div>
        <div style="color:var(--text-2);font-size:.85rem;margin-top:4px">${{arch.count}} pilots · ${{arch.share.toFixed(1)}}% of the meta</div>
      </div>
      <div style="display:flex;gap:1.5rem">
        <div style="text-align:center"><div style="font-size:1.8rem;font-weight:800;color:${{wrColor(arch.winrate)}}">${{arch.winrate.toFixed(1)}}%</div><div style="font-size:.7rem;color:var(--text-3);text-transform:uppercase">Win Rate</div></div>
        <div style="text-align:center"><div style="font-size:1.8rem;font-weight:800;color:var(--text-0)">${{arch.total}}</div><div style="font-size:.7rem;color:var(--text-3);text-transform:uppercase">Matches</div></div>
        <div style="text-align:center"><div style="font-size:1.4rem;font-weight:700"><span style="color:var(--green)">${{arch.wins}}</span> - <span style="color:var(--red)">${{arch.losses}}</span> - <span style="color:var(--yellow)">${{arch.draws}}</span></div><div style="font-size:.7rem;color:var(--text-3);text-transform:uppercase">W - L - D</div></div>
      </div>
    </div>`;

  // Matchup bars
  const ri = MX_DECKS.indexOf(name);
  const matchups = [];
  if (ri >= 0) {{
    MX_DECKS.forEach(n2 => {{
      if (n2 === name) return;
      const cell = MX_DATA[ri][n2];
      if (!cell || cell.mirror || cell.t < minS) return;
      matchups.push({{ name: n2, ...cell }});
    }});
  }}
  matchups.sort((a, b) => b.wr - a.wr);

  const container = document.getElementById('dd-matchups');
  if (matchups.length === 0) {{
    container.innerHTML = '<p style="color:var(--text-3)">No matchup data with enough sample size.</p>';
    return;
  }}

  // Split into favorable/unfavorable
  const good = matchups.filter(m => m.wr >= 50);
  const bad = matchups.filter(m => m.wr < 50).reverse();

  let html = '<div class="deck-detail-grid">';

  // Favorable
  html += '<div class="card"><div class="card-header"><h2 style="color:var(--green);font-size:.9rem">✦ Favorable Matchups</h2></div><div class="card-body">';
  if (good.length === 0) html += '<div style="color:var(--text-3);font-size:.85rem">None with enough data</div>';
  good.forEach(m => {{
    const cls = m.wr >= 55 ? 'good' : 'ok';
    html += `<div class="matchup-bar">
      <div class="opp">${{esc(m.name)}}</div>
      <div class="bar-bg"><div class="bar-fill ${{cls}}" style="width:${{clamp(m.wr, 0, 100)}}%"></div></div>
      <div class="wr-val" style="color:${{wrColor(m.wr)}}">${{m.wr}}%</div>
      <div class="rec">${{m.w}}-${{m.l}}-${{m.d}}</div>
    </div>`;
  }});
  html += '</div></div>';

  // Unfavorable
  html += '<div class="card"><div class="card-header"><h2 style="color:var(--red);font-size:.9rem">✦ Unfavorable Matchups</h2></div><div class="card-body">';
  if (bad.length === 0) html += '<div style="color:var(--text-3);font-size:.85rem">None with enough data</div>';
  bad.forEach(m => {{
    const cls = m.wr >= 45 ? 'ok' : 'bad';
    html += `<div class="matchup-bar">
      <div class="opp">${{esc(m.name)}}</div>
      <div class="bar-bg"><div class="bar-fill ${{cls}}" style="width:${{clamp(m.wr, 0, 100)}}%"></div></div>
      <div class="wr-val" style="color:${{wrColor(m.wr)}}">${{m.wr}}%</div>
      <div class="rec">${{m.w}}-${{m.l}}-${{m.d}}</div>
    </div>`;
  }});
  html += '</div></div>';

  html += '</div>';
  container.innerHTML = html;
}}

document.getElementById('dd-select').addEventListener('change', renderDetail);
document.getElementById('dd-min').addEventListener('input', renderDetail);

/* ══════════════════════════════════════════════════
   KEYBOARD SHORTCUTS
   ══════════════════════════════════════════════════ */
document.addEventListener('keydown', e => {{
  if (e.target.tagName === 'INPUT') return;
  if (e.key === '0') switchPanel('home-panel');
  if (e.key === '1' && currentTournamentId) switchPanel('meta-panel');
  if (e.key === '2' && currentTournamentId) switchPanel('matrix-panel');
  if (e.key === '3' && currentTournamentId) switchPanel('detail-panel');
}});

/* ══════════════════════════════════════════════════
   ADD TOURNAMENT — API-powered (home panel)
   ══════════════════════════════════════════════════ */
const statusEl = document.getElementById('add-status');
const progressWrap = document.getElementById('progress-wrap');
const progressFill = document.getElementById('progress-fill');
const progressText = document.getElementById('progress-text');
const progressPct = document.getElementById('progress-pct');

function showStatus(msg, type) {{
  statusEl.className = 'add-status ' + type;
  statusEl.innerHTML = msg;
}}

function setProgress(pct, text) {{
  progressFill.classList.remove('indeterminate');
  progressFill.style.width = pct + '%';
  progressPct.textContent = Math.round(pct) + '%';
  if (text) progressText.textContent = text;
}}

function setStep(stepName, state) {{
  const el = document.querySelector(`.progress-step[data-step="${{stepName}}"]`);
  if (el) el.className = 'progress-step ' + state;
}}

function resetProgress() {{
  progressWrap.classList.remove('active');
  progressFill.style.width = '0%';
  progressFill.classList.remove('indeterminate');
  progressPct.textContent = '0%';
  progressText.textContent = 'Preparando...';
  document.querySelectorAll('.progress-step').forEach(s => s.className = 'progress-step');
}}

// Simulate progress during long scrape
let progressTimer = null;
function startSimulatedProgress() {{
  let pct = 5;
  const steps = [
    {{at: 5, step: 'validate', text: 'Validando URL...'}},
    {{at: 10, step: 'connect', text: 'Conectando con melee.gg...'}},
    {{at: 20, step: 'rounds', text: 'Descargando info de rondas...'}},
    {{at: 35, step: 'matches', text: 'Procesando partidas... esto tarda 1-2 min'}},
    {{at: 65, step: 'matrix', text: 'Construyendo matrix de matchups...'}},
    {{at: 85, step: 'save', text: 'Guardando datos y regenerando sitio...'}},
  ];
  let stepIdx = 0;

  setProgress(5, 'Validando URL...');
  setStep('validate', 'active');

  progressTimer = setInterval(() => {{
    // Slow down as we get higher (simulate long wait)
    const increment = pct < 30 ? 1.2 : pct < 60 ? 0.4 : pct < 80 ? 0.15 : 0.05;
    pct = Math.min(pct + increment, 92);
    setProgress(pct);

    // Advance step markers
    while (stepIdx < steps.length && pct >= steps[stepIdx].at) {{
      if (stepIdx > 0) setStep(steps[stepIdx - 1].step, 'done');
      setStep(steps[stepIdx].step, 'active');
      progressText.textContent = steps[stepIdx].text;
      stepIdx++;
    }}
  }}, 800);
}}

function stopSimulatedProgress() {{
  if (progressTimer) {{ clearInterval(progressTimer); progressTimer = null; }}
}}

// Añadir torneo via API
document.getElementById('btn-do-add').addEventListener('click', async () => {{
  const url = document.getElementById('add-url').value.trim();
  if (!url) {{ showStatus('⚠️ Pega una URL de melee.gg', 'error'); return; }}

  // Validate URL format
  if (!url.match(/melee[.]gg/i) && !url.match(/^\d+$/)) {{
    showStatus('⚠️ Introduce una URL válida de melee.gg o un ID numérico de torneo.', 'error');
    return;
  }}

  const btn = document.getElementById('btn-do-add');
  btn.disabled = true;
  btn.innerHTML = '<span class="add-spinner"></span> Scrapeando...';
  document.getElementById('add-url').disabled = true;
  statusEl.className = 'add-status';
  statusEl.innerHTML = '';

  // Show and start progress bar
  resetProgress();
  progressWrap.classList.add('active');
  startSimulatedProgress();

  try {{
    // 3-minute timeout for scraping
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 180000);

    const res = await fetch('/api/tournaments/add-sync', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ url: url }}),
      signal: controller.signal,
    }});
    clearTimeout(timeout);
    const data = await res.json();

    stopSimulatedProgress();

    if (data.ok) {{
      // Complete the progress bar
      document.querySelectorAll('.progress-step').forEach(s => s.className = 'progress-step done');
      setProgress(100, '¡Completado!');

      if (data.action === 'already_exists') {{
        showStatus(`✅ <strong>${{data.name}}</strong> — ya estaba cargado. Recargando...`, 'success');
      }} else {{
        showStatus(`🎉 ¡Añadido! <strong>${{data.name}}</strong> — ${{data.total_matches}} partidas, ${{data.total_players || '?'}} jugadores. Recargando...`, 'success');
      }}
      setTimeout(() => window.location.reload(), 1500);
      return;
    }} else {{
      document.querySelectorAll('.progress-step.active').forEach(s => s.className = 'progress-step error');
      setProgress(0, 'Error');
      showStatus(`❌ Error: ${{data.error}}`, 'error');
    }}
  }} catch (err) {{
    stopSimulatedProgress();
    document.querySelectorAll('.progress-step.active').forEach(s => s.className = 'progress-step error');
    if (err.name === 'AbortError') {{
      showStatus('❌ Timeout: el scraping tardó más de 3 minutos. Inténtalo de nuevo.', 'error');
    }} else {{
      showStatus(`❌ No se pudo conectar con el servidor. Asegúrate de lanzar <code>python server.py</code>.`, 'error');
    }}
  }}

  btn.disabled = false;
  btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg> Scrapear';
  document.getElementById('add-url').disabled = false;
}});

// Enter en el input
document.getElementById('add-url').addEventListener('keydown', e => {{
  if (e.key === 'Enter') document.getElementById('btn-do-add').click();
}});

</script>
</body>
</html>"""


def generate_multi_tournament_site(tournaments: dict, output_dir: str = "dist"):
    """
    Generate the static site with multiple tournaments.
    
    Args:
        tournaments: dict of {tournament_id: tournament_data_dict}
        output_dir: output directory for the site
    """
    all_metas = {}
    for tid, tdata in tournaments.items():
        print(f"[*] Procesando: {tdata['tournament']['name']}")
        meta = build_metagame(tdata)
        all_metas[tid] = meta
        active = len([a for a in meta['archetypes'] if a['total'] > 0])
        print(f"    Arquetipos con partidas: {active}")

    os.makedirs(output_dir, exist_ok=True)

    html = generate_html(all_metas)
    out_path = os.path.join(output_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    redirects_path = os.path.join(output_dir, "_redirects")
    with open(redirects_path, "w") as f:
        f.write("/*    /index.html   200\n")

    print(f"\n[✓] Sitio generado en: {output_dir}/")
    print(f"    index.html  ({len(html):,} bytes)")
    print(f"    Torneos embebidos: {len(all_metas)}")
    for tid, meta in all_metas.items():
        t = meta['tournament']
        print(f"      • {t['name']} ({t['total_matches']} matches)")


def main():
    parser = argparse.ArgumentParser(description="Generate static Netlify site from tournament data")
    parser.add_argument("--input", "-i", default=None,
                        help="Input JSON file (legacy single-tournament mode)")
    parser.add_argument("--db", default=DB_FILE,
                        help="Tournament database file (default: tournaments_db.json)")
    parser.add_argument("--output", "-o", default="dist", help="Output directory")
    args = parser.parse_args()

    # Mode 1: Legacy single-tournament file
    if args.input:
        if not os.path.exists(args.input):
            print(f"[!] No se encuentra {args.input}")
            sys.exit(1)
        print(f"[*] Modo legacy — cargando: {args.input}")
        data = load_data(args.input)
        tid = str(data["tournament"]["id"])
        generate_multi_tournament_site({tid: data}, args.output)
        return

    # Mode 2: Multi-tournament database
    if os.path.exists(args.db):
        print(f"[*] Cargando base de datos: {args.db}")
        db_data = load_data(args.db)
        tournaments = db_data.get("tournaments", {})
        if tournaments:
            generate_multi_tournament_site(tournaments, args.output)
            return
        print(f"[!] Base de datos vacía en {args.db}")

    # Fallback: try legacy tournament_data.json
    legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tournament_data.json")
    if os.path.exists(legacy):
        print(f"[*] Fallback a archivo legacy: {legacy}")
        data = load_data(legacy)
        tid = str(data["tournament"]["id"])
        generate_multi_tournament_site({tid: data}, args.output)
        return

    print("[!] No se encontró ninguna fuente de datos.")
    print("    Opciones:")
    print("    1. python manage_tournaments.py add <url>")
    print("    2. python generate_site.py --input tournament_data.json")
    sys.exit(1)


if __name__ == "__main__":
    main()
