#!/usr/bin/env python3
"""
MTG Meta Analyzer para melee.gg

Descarga matchups + decklists de un torneo y genera:
  1. Agrupación de decks por arquetipo (basado en nombre de deck)
  2. Matriz de emparejamientos (archetype vs archetype)
  3. Metagame share (% de cada arquetipo)
  4. Dashboard HTML interactivo

Uso:
    python meta_analyzer.py                            # torneo 339227
    python meta_analyzer.py -t 339227                  # otro torneo
    python meta_analyzer.py --skip-decklists           # sin descargar cartas
    python meta_analyzer.py --min-matches 10           # umbral mínimo
"""

import argparse
import csv
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

# Importar scraper base
import melee_scraper as scraper


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
REQUEST_DELAY = 0.3
DECKLIST_BATCH_DELAY = 0.2


# ---------------------------------------------------------------------------
# Modelos adicionales
# ---------------------------------------------------------------------------
@dataclass
class DecklistCard:
    name: str
    quantity: int
    card_type: str
    component: str  # "main", "sideboard", "companion"


@dataclass
class Decklist:
    id: str
    name: str
    format_name: str
    player_name: str
    cards: list  # [DecklistCard]


@dataclass
class Archetype:
    name: str
    deck_names: list  # nombres originales agrupados
    count: int  # nº de apariciones en matches
    decklists: list  # [Decklist]
    wins: int = 0
    losses: int = 0
    draws: int = 0

    @property
    def total_matches(self):
        return self.wins + self.losses + self.draws

    @property
    def winrate(self):
        return self.wins / self.total_matches * 100 if self.total_matches > 0 else 0


# ---------------------------------------------------------------------------
# Descarga de decklists
# ---------------------------------------------------------------------------
def fetch_decklist_details(decklist_id: str, ctx: ssl.SSLContext) -> Optional[dict]:
    """Descarga los detalles de una decklist (cartas) desde melee.gg."""
    url = f"{scraper.BASE_URL}/Decklist/GetDecklistDetails?id={decklist_id}"
    req = urllib.request.Request(url, headers={
        "User-Agent": scraper.USER_AGENT,
        "Accept": "application/json, */*",
        "X-Requested-With": "XMLHttpRequest",
    })
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            if text.strip().startswith("{"):
                return json.loads(text)
    except Exception:
        pass
    return None


def parse_decklist(raw: dict, player_name: str = "") -> Decklist:
    """Parsea la respuesta JSON de GetDecklistDetails."""
    COMPONENTS = {0: "main", 99: "sideboard", 1: "companion"}
    cards = []
    for rec in raw.get("Records", []):
        cards.append(DecklistCard(
            name=rec.get("n", "Unknown"),
            quantity=rec.get("q", 0),
            card_type=rec.get("t", "Unknown"),
            component=COMPONENTS.get(rec.get("c", 0), "main"),
        ))
    return Decklist(
        id=raw.get("Guid", ""),
        name=raw.get("DecklistName", "Unknown"),
        format_name=raw.get("FormatName", ""),
        player_name=player_name,
        cards=cards,
    )


def collect_unique_decklists(tournament: scraper.TournamentData) -> dict:
    """
    Recolecta todos los decklist IDs únicos de los matches.
    Retorna {decklist_id: (deck_name, player_name)}.
    """
    unique = {}
    for match in tournament.matches:
        if match.is_bye:
            continue
        competitors = []
        if match.player1 and match.player1_decklist:
            competitors.append((match.player1, match.player1_decklist))
        if match.player2 and match.player2_decklist:
            competitors.append((match.player2, match.player2_decklist))

        # Necesitamos los IDs de decklist del JSON raw
        # Los guardamos en el parse del match
    return unique


def extract_decklist_ids_from_matches(tournament: scraper.TournamentData) -> dict:
    """
    Re-descarga las rondas y extrae decklist IDs.
    Retorna {decklist_id: {'name': str, 'player': str, 'deck_name': str}}.
    """
    # Los decklist IDs están disponibles en el raw JSON,
    # pero no los guardamos antes. Vamos a re-extraerlos.
    ctx = scraper._ssl_context()
    deck_ids = {}

    print("[*] Extrayendo IDs de decklists de los matches...")
    for round_id, round_name in tournament.rounds.items():
        print(f"  [>] {round_name}...", end=" ", flush=True)
        try:
            raw_matches = scraper.fetch_round_matches(round_id, tournament.tournament_id, ctx)
            for m in raw_matches:
                for comp in m.get("Competitors", []):
                    team = comp.get("Team", {})
                    players = team.get("Players", [])
                    player_name = players[0].get("DisplayName", "") if players else ""
                    for dl in comp.get("Decklists", []):
                        did = dl.get("DecklistId")
                        if did and did not in deck_ids:
                            deck_ids[did] = {
                                "name": dl.get("DecklistName", "Unknown"),
                                "player": player_name,
                                "format": dl.get("Format", ""),
                            }
            print(f"{len(raw_matches)} matches")
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(REQUEST_DELAY)

    print(f"[*] Decklists únicas encontradas: {len(deck_ids)}")
    return deck_ids


def download_all_decklists(deck_ids: dict, max_decks: int = 0) -> list:
    """
    Descarga las cartas de cada decklist única.
    Retorna lista de Decklist.
    """
    ctx = scraper._ssl_context()
    decklists = []
    items = list(deck_ids.items())
    if max_decks > 0:
        items = items[:max_decks]

    total = len(items)
    print(f"[*] Descargando {total} decklists...")

    for i, (did, info) in enumerate(items):
        if (i + 1) % 50 == 0 or i == 0:
            print(f"  [{i+1}/{total}] {info['name']} ({info['player']})...")

        raw = fetch_decklist_details(did, ctx)
        if raw and raw.get("Records"):
            dl = parse_decklist(raw, info["player"])
            decklists.append(dl)
        time.sleep(DECKLIST_BATCH_DELAY)

    print(f"[*] Decklists descargadas: {len(decklists)}/{total}")
    return decklists


# ---------------------------------------------------------------------------
# Agrupación en arquetipos
# ---------------------------------------------------------------------------
def normalize_deck_name(name: str) -> str:
    """Normaliza un nombre de deck para agrupación en arquetipo."""
    name = name.strip()
    # Remover variaciones comunes
    name = re.sub(r'\s*\(.*?\)\s*', '', name)  # (variant)
    name = re.sub(r'\s*v\d+\s*$', '', name, flags=re.I)  # v2, v3
    name = re.sub(r'\s*#\d+\s*$', '', name)  # #2
    name = re.sub(r'\s+', ' ', name).strip()
    return name


# Mapeo manual de nombres de deck a arquetipos conocidos
# Se puede ampliar o personalizar
ARCHETYPE_ALIASES = {
    # Izzet variants
    "Izzet Lessons": "Izzet Prowess",
    "Izzet Spellementals": "Izzet Prowess",
    "Izzet Elementals": "Izzet Prowess",
    "UR Prowess": "Izzet Prowess",
    "UR Lessons": "Izzet Prowess",
    "Izzet Spells": "Izzet Prowess",
    # Dimir variants
    "Dimir Excruciator": "Dimir Midrange",
    "Dimir Control": "Dimir Midrange",
    "UB Midrange": "Dimir Midrange",
    "UB Control": "Dimir Midrange",
    # Mono-Green variants
    "Mono-Green Landfall": "Mono-Green Aggro",
    "Mono Green Landfall": "Mono-Green Aggro",
    "Mono Green Aggro": "Mono-Green Aggro",
    "Mono-Green Ramp": "Mono-Green Aggro",
    # Simic variants
    "Simic Rhythm": "Simic Midrange",
    "Simic Ramp": "Simic Midrange",
    "UG Midrange": "Simic Midrange",
    # Azorius variants
    "Azorius Tempo": "Azorius Tempo",
    "Azorius Control": "Azorius Control",
    "UW Tempo": "Azorius Tempo",
    "UW Control": "Azorius Control",
    # Boros variants
    "Boros Dragons": "Boros Dragons",
    "Boros Aggro": "Boros Aggro",
    "RW Aggro": "Boros Aggro",
    # Jeskai
    "Jeskai Control": "Jeskai Control",
    # Temur
    "Temur Harmonizer": "Temur Midrange",
    "Temur Midrange": "Temur Midrange",
    # Bant
    "Bant Airbending": "Bant Midrange",
    "Bant Midrange": "Bant Midrange",
    # Mono-Red
    "Mono-Red Aggro": "Mono-Red Aggro",
    "Mono Red Aggro": "Mono-Red Aggro",
    "RDW": "Mono-Red Aggro",
    # Rakdos
    "Rakdos Monument": "Rakdos Midrange",
    "Rakdos Midrange": "Rakdos Midrange",
}


def classify_archetype(deck_name: str) -> str:
    """
    Clasifica un nombre de deck en su arquetipo.
    Primero busca en aliases, luego usa el nombre normalizado.
    """
    normalized = normalize_deck_name(deck_name)

    # Búsqueda exacta en aliases
    if normalized in ARCHETYPE_ALIASES:
        return ARCHETYPE_ALIASES[normalized]

    # Búsqueda case-insensitive
    for alias, archetype in ARCHETYPE_ALIASES.items():
        if normalized.lower() == alias.lower():
            return archetype

    # Si no se encuentra, usar el nombre normalizado
    return normalized


def build_archetype_map(tournament: scraper.TournamentData) -> dict:
    """
    Construye el mapeo de arquetipos a partir de los matches.
    Retorna {deck_name_original: archetype_name}.
    """
    deck_names = set()
    for match in tournament.matches:
        if match.player1_decklist:
            deck_names.add(match.player1_decklist)
        if match.player2_decklist:
            deck_names.add(match.player2_decklist)

    mapping = {}
    for name in deck_names:
        mapping[name] = classify_archetype(name)
    return mapping


# ---------------------------------------------------------------------------
# Análisis de metagame
# ---------------------------------------------------------------------------
@dataclass
class MetagameData:
    archetypes: dict  # {arch_name: Archetype}
    matchup_matrix: dict  # {arch1: {arch2: {wins, losses, draws, total}}}
    meta_share: dict  # {arch_name: float %}
    total_players: int
    total_matches: int
    deck_to_archetype: dict  # {deck_name: arch_name}
    decklists: dict  # {deck_name: [Decklist]}


def analyze_metagame(
    tournament: scraper.TournamentData,
    archetype_map: dict,
    decklists: Optional[list] = None,
) -> MetagameData:
    """Realiza el análisis completo del metagame."""

    # Contar apariciones por arquetipo (cuántos jugadores lo usan)
    player_archetypes = {}  # {player_id: archetype}
    for match in tournament.matches:
        if match.player1 and match.player1_decklist:
            arch = archetype_map.get(match.player1_decklist, match.player1_decklist)
            player_archetypes[match.player1.id] = arch
        if match.player2 and match.player2_decklist:
            arch = archetype_map.get(match.player2_decklist, match.player2_decklist)
            player_archetypes[match.player2.id] = arch

    # Meta share
    arch_counts = Counter(player_archetypes.values())
    total_players_with_deck = len(player_archetypes)
    meta_share = {
        arch: count / total_players_with_deck * 100
        for arch, count in arch_counts.items()
    }

    # Matchup matrix
    matchups = defaultdict(lambda: defaultdict(lambda: {
        "wins": 0, "losses": 0, "draws": 0, "total": 0
    }))

    # Stats per archetype
    arch_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "draws": 0})

    for match in tournament.matches:
        if match.is_bye or not match.player1 or not match.player2:
            continue
        if not match.player1_decklist or not match.player2_decklist:
            continue

        a1 = archetype_map.get(match.player1_decklist, match.player1_decklist)
        a2 = archetype_map.get(match.player2_decklist, match.player2_decklist)

        if match.player1_wins > match.player2_wins:
            matchups[a1][a2]["wins"] += 1
            matchups[a2][a1]["losses"] += 1
            arch_stats[a1]["wins"] += 1
            arch_stats[a2]["losses"] += 1
        elif match.player2_wins > match.player1_wins:
            matchups[a1][a2]["losses"] += 1
            matchups[a2][a1]["wins"] += 1
            arch_stats[a1]["losses"] += 1
            arch_stats[a2]["wins"] += 1
        else:
            matchups[a1][a2]["draws"] += 1
            matchups[a2][a1]["draws"] += 1
            arch_stats[a1]["draws"] += 1
            arch_stats[a2]["draws"] += 1

        matchups[a1][a2]["total"] += 1
        matchups[a2][a1]["total"] += 1

    # Construct Archetype objects
    archetypes = {}
    for arch_name in sorted(meta_share.keys(), key=lambda x: meta_share[x], reverse=True):
        deck_names = [dn for dn, an in archetype_map.items() if an == arch_name]
        s = arch_stats[arch_name]
        archetypes[arch_name] = Archetype(
            name=arch_name,
            deck_names=sorted(set(deck_names)),
            count=arch_counts[arch_name],
            decklists=[],
            wins=s["wins"],
            losses=s["losses"],
            draws=s["draws"],
        )

    # Attach decklists
    decklists_by_deck = defaultdict(list)
    if decklists:
        for dl in decklists:
            decklists_by_deck[dl.name].append(dl)
        for arch_name, arch in archetypes.items():
            for dn in arch.deck_names:
                arch.decklists.extend(decklists_by_deck.get(dn, []))

    return MetagameData(
        archetypes=archetypes,
        matchup_matrix=dict(matchups),
        meta_share=meta_share,
        total_players=total_players_with_deck,
        total_matches=len([m for m in tournament.matches if not m.is_bye]),
        deck_to_archetype=archetype_map,
        decklists=dict(decklists_by_deck),
    )


# ---------------------------------------------------------------------------
# Exportación de decklists
# ---------------------------------------------------------------------------
def export_decklists_csv(decklists: list, filepath: str):
    """Exporta todas las decklists con sus cartas a CSV."""
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "DecklistID", "DeckName", "PlayerName", "Format",
            "Component", "CardName", "Quantity", "CardType",
        ])
        for dl in decklists:
            for card in dl.cards:
                writer.writerow([
                    dl.id, dl.name, dl.player_name, dl.format_name,
                    card.component, card.name, card.quantity, card.card_type,
                ])
    print(f"[*] Decklists exportadas a: {filepath}")


def export_archetype_summary_csv(meta: MetagameData, filepath: str):
    """Exporta resumen de arquetipos con deck names agrupados."""
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Archetype", "MetaShare%", "Players", "Wins", "Losses", "Draws",
            "TotalMatches", "Winrate%", "DeckNames",
        ])
        for arch_name, arch in sorted(
            meta.archetypes.items(),
            key=lambda x: x[1].count,
            reverse=True,
        ):
            writer.writerow([
                arch_name,
                f"{meta.meta_share.get(arch_name, 0):.1f}",
                arch.count,
                arch.wins,
                arch.losses,
                arch.draws,
                arch.total_matches,
                f"{arch.winrate:.1f}",
                " | ".join(arch.deck_names),
            ])
    print(f"[*] Resumen de arquetipos exportado a: {filepath}")


# ---------------------------------------------------------------------------
# Generación de HTML interactivo (estilo j6e meta analyzer)
# ---------------------------------------------------------------------------
def generate_html_dashboard(
    meta: MetagameData,
    tournament: scraper.TournamentData,
    filepath: str,
):
    """Genera un dashboard HTML interactivo con la matriz de emparejamientos."""

    # Filtrar arquetipos con suficientes partidas para la matriz
    MIN_FOR_MATRIX = 3
    sorted_archs = sorted(
        meta.archetypes.values(),
        key=lambda a: a.count,
        reverse=True,
    )
    matrix_archs = [a for a in sorted_archs if a.total_matches >= MIN_FOR_MATRIX]
    matrix_names = [a.name for a in matrix_archs]

    # Preparar datos para JS
    matrix_js_data = []
    for a1 in matrix_names:
        row = {}
        for a2 in matrix_names:
            if a1 == a2:
                row[a2] = {"type": "mirror"}
            else:
                stats = meta.matchup_matrix.get(a1, {}).get(a2, {})
                w = stats.get("wins", 0)
                l = stats.get("losses", 0)
                d = stats.get("draws", 0)
                t = w + l + d
                wr = w / t * 100 if t > 0 else 50
                row[a2] = {"wins": w, "losses": l, "draws": d, "total": t, "winrate": round(wr, 1)}
        matrix_js_data.append(row)

    meta_share_data = []
    for arch in sorted_archs:
        share = meta.meta_share.get(arch.name, 0)
        meta_share_data.append({
            "name": arch.name,
            "count": arch.count,
            "share": round(share, 1),
            "wins": arch.wins,
            "losses": arch.losses,
            "draws": arch.draws,
            "winrate": round(arch.winrate, 1),
            "deckNames": arch.deck_names,
            "totalMatches": arch.total_matches,
        })

    # Datos de decklists por arquetipo
    decklists_data = {}
    for arch_name, arch in meta.archetypes.items():
        if arch.decklists:
            decklists_data[arch_name] = []
            for dl in arch.decklists[:10]:  # max 10 listas por arquetipo
                cards = [
                    {"name": c.name, "qty": c.quantity, "type": c.card_type, "component": c.component}
                    for c in dl.cards
                ]
                decklists_data[arch_name].append({
                    "name": dl.name,
                    "player": dl.player_name,
                    "cards": cards,
                })

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Meta Analyzer — {tournament.tournament_name}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e0e0e0; }}
.header {{ background: linear-gradient(135deg, #1a1d2e 0%, #2d1f4e 100%); padding: 2rem; text-align: center; border-bottom: 2px solid #3b3f5c; }}
.header h1 {{ font-size: 1.8rem; color: #fff; margin-bottom: 0.5rem; }}
.header .subtitle {{ color: #9ca3af; font-size: 0.95rem; }}
.stats-bar {{ display: flex; justify-content: center; gap: 2rem; padding: 1rem 2rem; background: #161822; border-bottom: 1px solid #2a2d3d; flex-wrap: wrap; }}
.stat {{ text-align: center; }}
.stat .value {{ font-size: 1.5rem; font-weight: 700; color: #60a5fa; }}
.stat .label {{ font-size: 0.75rem; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em; }}
.container {{ max-width: 1600px; margin: 0 auto; padding: 1.5rem; }}
.tabs {{ display: flex; gap: 0; margin-bottom: 1.5rem; border-bottom: 2px solid #2a2d3d; }}
.tab {{ padding: 0.75rem 1.5rem; cursor: pointer; border: none; background: none; color: #9ca3af; font-size: 0.95rem; font-weight: 500; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.2s; }}
.tab:hover {{ color: #e0e0e0; }}
.tab.active {{ color: #60a5fa; border-bottom-color: #60a5fa; }}
.panel {{ display: none; }}
.panel.active {{ display: block; }}

/* Metagame table */
.meta-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
.meta-table th {{ text-align: left; padding: 0.6rem 0.8rem; color: #9ca3af; font-weight: 600; border-bottom: 1px solid #2a2d3d; cursor: pointer; user-select: none; }}
.meta-table th:hover {{ color: #60a5fa; }}
.meta-table td {{ padding: 0.5rem 0.8rem; border-bottom: 1px solid #1e2030; }}
.meta-table tr:hover {{ background: #1a1d2e; }}
.meta-table .deck-name {{ font-weight: 600; color: #e0e0e0; cursor: pointer; }}
.meta-table .deck-name:hover {{ color: #60a5fa; }}
.meta-table .deck-variants {{ color: #6b7280; font-size: 0.75rem; }}
.winrate-high {{ color: #34d399; }}
.winrate-mid {{ color: #fbbf24; }}
.winrate-low {{ color: #f87171; }}
.share-bar {{ display: inline-block; height: 6px; background: #60a5fa; border-radius: 3px; min-width: 2px; }}

/* Matrix */
.matrix-container {{ overflow-x: auto; margin: 0 -0.5rem; }}
.matrix {{ border-collapse: collapse; font-size: 0.72rem; white-space: nowrap; }}
.matrix th, .matrix td {{ padding: 4px 6px; text-align: center; border: 1px solid #1e2030; min-width: 65px; }}
.matrix th {{ background: #161822; color: #9ca3af; font-weight: 600; position: sticky; top: 0; z-index: 1; }}
.matrix th.row-header {{ text-align: left; position: sticky; left: 0; z-index: 2; background: #161822; max-width: 150px; overflow: hidden; text-overflow: ellipsis; }}
.matrix td.row-header {{ text-align: left; position: sticky; left: 0; background: #0f1117; font-weight: 600; color: #e0e0e0; max-width: 150px; overflow: hidden; text-overflow: ellipsis; z-index: 1; }}
.matrix .mirror {{ background: #1a1a2e; color: #4b5563; }}
.matrix .good {{ background: rgba(52, 211, 153, 0.15); color: #34d399; }}
.matrix .ok {{ background: rgba(251, 191, 36, 0.1); color: #fbbf24; }}
.matrix .bad {{ background: rgba(248, 113, 113, 0.15); color: #f87171; }}
.matrix .no-data {{ color: #374151; }}
.matrix td:hover {{ outline: 2px solid #60a5fa; outline-offset: -1px; }}

/* Decklist panel */
.decklist-section {{ margin-bottom: 2rem; }}
.arch-header {{ display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; padding: 0.75rem 1rem; background: #161822; border-radius: 8px; cursor: pointer; }}
.arch-header h3 {{ font-size: 1.1rem; }}
.arch-header .badge {{ background: #2563eb; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; }}
.deck-cards {{ display: none; padding: 0.5rem 1rem; }}
.deck-cards.open {{ display: block; }}
.card-list {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1rem; }}
.deck-card {{ background: #161822; border-radius: 8px; padding: 1rem; border: 1px solid #2a2d3d; }}
.deck-card h4 {{ color: #60a5fa; margin-bottom: 0.5rem; font-size: 0.95rem; }}
.deck-card .player {{ color: #9ca3af; font-size: 0.8rem; margin-bottom: 0.75rem; }}
.card-section-title {{ color: #fbbf24; font-weight: 600; font-size: 0.8rem; margin: 0.5rem 0 0.25rem; }}
.card-entry {{ font-size: 0.8rem; color: #d1d5db; padding: 1px 0; }}
.card-entry .qty {{ color: #60a5fa; font-weight: 600; display: inline-block; width: 1.5rem; }}

/* Tooltip */
.tooltip {{ position: fixed; background: #1e2030; border: 1px solid #3b3f5c; border-radius: 6px; padding: 0.5rem 0.75rem; font-size: 0.8rem; pointer-events: none; z-index: 1000; max-width: 250px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }}
.tooltip .tt-title {{ font-weight: 600; color: #60a5fa; margin-bottom: 0.25rem; }}
.tooltip .tt-stat {{ color: #d1d5db; }}

/* Filter */
.filter-row {{ display: flex; gap: 1rem; align-items: center; margin-bottom: 1rem; flex-wrap: wrap; }}
.filter-row label {{ color: #9ca3af; font-size: 0.85rem; }}
.filter-row input, .filter-row select {{ background: #161822; border: 1px solid #2a2d3d; color: #e0e0e0; padding: 0.4rem 0.6rem; border-radius: 4px; font-size: 0.85rem; }}
</style>
</head>
<body>

<div class="header">
    <h1>MTG Meta Analyzer</h1>
    <div class="subtitle">{tournament.tournament_name}</div>
</div>

<div class="stats-bar">
    <div class="stat"><div class="value">{meta.total_players}</div><div class="label">Jugadores</div></div>
    <div class="stat"><div class="value">{meta.total_matches}</div><div class="label">Partidas</div></div>
    <div class="stat"><div class="value">{len(meta.archetypes)}</div><div class="label">Arquetipos</div></div>
    <div class="stat"><div class="value">{len(tournament.rounds)}</div><div class="label">Rondas</div></div>
</div>

<div class="container">
    <div class="tabs">
        <div class="tab active" data-panel="metagame">Metagame</div>
        <div class="tab" data-panel="matrix">Matchup Matrix</div>
        <div class="tab" data-panel="decklists">Decklists</div>
    </div>

    <!-- METAGAME PANEL -->
    <div id="metagame" class="panel active">
        <div class="filter-row">
            <label>Min partidas:</label>
            <input type="number" id="min-matches-filter" value="5" min="1" max="100" style="width:60px">
            <label>Buscar:</label>
            <input type="text" id="meta-search" placeholder="Filtrar arquetipos..." style="width:200px">
        </div>
        <table class="meta-table" id="meta-table">
            <thead>
                <tr>
                    <th data-sort="name">#</th>
                    <th data-sort="name">Archetype</th>
                    <th data-sort="share">Meta %</th>
                    <th data-sort="count">Pilots</th>
                    <th data-sort="wins">W</th>
                    <th data-sort="losses">L</th>
                    <th data-sort="draws">D</th>
                    <th data-sort="total">Matches</th>
                    <th data-sort="winrate">Win %</th>
                    <th data-sort="name">Deck Names</th>
                </tr>
            </thead>
            <tbody id="meta-body"></tbody>
        </table>
    </div>

    <!-- MATRIX PANEL -->
    <div id="matrix" class="panel">
        <div class="filter-row">
            <label>Min partidas para mostrar celda:</label>
            <input type="number" id="matrix-min-matches" value="3" min="1" max="50" style="width:60px">
        </div>
        <div class="matrix-container">
            <table class="matrix" id="matrix-table"></table>
        </div>
    </div>

    <!-- DECKLISTS PANEL -->
    <div id="decklists" class="panel">
        <div class="filter-row">
            <label>Buscar arquetipo:</label>
            <input type="text" id="deck-search" placeholder="Filtrar..." style="width:200px">
        </div>
        <div id="decklists-container"></div>
    </div>
</div>

<div class="tooltip" id="tooltip" style="display:none"></div>

<script>
// ===================== DATA =====================
const META_DATA = {json.dumps(meta_share_data, ensure_ascii=False)};
const MATRIX_NAMES = {json.dumps(matrix_names, ensure_ascii=False)};
const MATRIX_DATA = {json.dumps(matrix_js_data, ensure_ascii=False)};
const DECKLISTS_DATA = {json.dumps(decklists_data, ensure_ascii=False)};

// ===================== TABS =====================
document.querySelectorAll('.tab').forEach(tab => {{
    tab.addEventListener('click', () => {{
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(tab.dataset.panel).classList.add('active');
    }});
}});

// ===================== METAGAME TABLE =====================
function wrClass(wr) {{ return wr >= 55 ? 'winrate-high' : wr >= 45 ? 'winrate-mid' : 'winrate-low'; }}
function renderMetaTable() {{
    const minM = parseInt(document.getElementById('min-matches-filter').value) || 0;
    const search = document.getElementById('meta-search').value.toLowerCase();
    const tbody = document.getElementById('meta-body');
    let filtered = META_DATA.filter(a => a.totalMatches >= minM && a.name.toLowerCase().includes(search));
    const maxShare = Math.max(...filtered.map(a => a.share), 1);
    tbody.innerHTML = filtered.map((a, i) => `
        <tr>
            <td>${{i+1}}</td>
            <td>
                <div class="deck-name">${{a.name}}</div>
                <div class="deck-variants">${{a.deckNames.join(', ')}}</div>
            </td>
            <td><span class="share-bar" style="width:${{a.share/maxShare*80}}px"></span> ${{a.share}}%</td>
            <td>${{a.count}}</td>
            <td>${{a.wins}}</td>
            <td>${{a.losses}}</td>
            <td>${{a.draws}}</td>
            <td>${{a.totalMatches}}</td>
            <td class="${{wrClass(a.winrate)}}">${{a.winrate}}%</td>
            <td class="deck-variants">${{a.deckNames.length}} variants</td>
        </tr>
    `).join('');
}}
document.getElementById('min-matches-filter').addEventListener('input', renderMetaTable);
document.getElementById('meta-search').addEventListener('input', renderMetaTable);
renderMetaTable();

// ===================== MATRIX =====================
function renderMatrix() {{
    const minM = parseInt(document.getElementById('matrix-min-matches').value) || 1;
    const table = document.getElementById('matrix-table');
    let html = '<thead><tr><th class="row-header">VS</th>';
    MATRIX_NAMES.forEach(n => {{ html += `<th title="${{n}}">${{n.length > 15 ? n.slice(0,14)+'…' : n}}</th>`; }});
    html += '</tr></thead><tbody>';
    MATRIX_NAMES.forEach((n1, i) => {{
        html += `<tr><td class="row-header" title="${{n1}}">${{n1.length > 18 ? n1.slice(0,17)+'…' : n1}}</td>`;
        MATRIX_NAMES.forEach((n2, j) => {{
            const cell = MATRIX_DATA[i][n2];
            if (cell.type === 'mirror') {{
                html += '<td class="mirror">—</td>';
            }} else if (cell.total < minM) {{
                html += '<td class="no-data">—</td>';
            }} else {{
                const cls = cell.winrate >= 55 ? 'good' : cell.winrate >= 45 ? 'ok' : 'bad';
                html += `<td class="${{cls}}" data-a1="${{n1}}" data-a2="${{n2}}" data-w="${{cell.wins}}" data-l="${{cell.losses}}" data-d="${{cell.draws}}" data-t="${{cell.total}}" data-wr="${{cell.winrate}}">${{cell.winrate}}%<br><span style="font-size:0.65rem;opacity:0.7">${{cell.wins}}-${{cell.losses}}-${{cell.draws}}</span></td>`;
            }}
        }});
        html += '</tr>';
    }});
    html += '</tbody>';
    table.innerHTML = html;

    // Tooltips
    table.querySelectorAll('td[data-a1]').forEach(td => {{
        td.addEventListener('mouseenter', e => {{
            const tt = document.getElementById('tooltip');
            tt.innerHTML = `<div class="tt-title">${{td.dataset.a1}} vs ${{td.dataset.a2}}</div>
                <div class="tt-stat">Win rate: ${{td.dataset.wr}}%</div>
                <div class="tt-stat">Record: ${{td.dataset.w}}-${{td.dataset.l}}-${{td.dataset.d}} (${{td.dataset.t}} matches)</div>`;
            tt.style.display = 'block';
            tt.style.left = (e.clientX + 12) + 'px';
            tt.style.top = (e.clientY + 12) + 'px';
        }});
        td.addEventListener('mouseleave', () => {{ document.getElementById('tooltip').style.display = 'none'; }});
        td.addEventListener('mousemove', e => {{
            const tt = document.getElementById('tooltip');
            tt.style.left = (e.clientX + 12) + 'px';
            tt.style.top = (e.clientY + 12) + 'px';
        }});
    }});
}}
document.getElementById('matrix-min-matches').addEventListener('input', renderMatrix);
renderMatrix();

// ===================== DECKLISTS =====================
function renderDecklists() {{
    const search = document.getElementById('deck-search').value.toLowerCase();
    const container = document.getElementById('decklists-container');
    let html = '';
    META_DATA.filter(a => a.name.toLowerCase().includes(search)).forEach(arch => {{
        const lists = DECKLISTS_DATA[arch.name] || [];
        html += `<div class="decklist-section">
            <div class="arch-header" onclick="this.nextElementSibling.classList.toggle('open')">
                <h3>${{arch.name}}</h3>
                <span class="badge">${{arch.share}}% meta</span>
                <span class="badge" style="background:#065f46">${{arch.winrate}}% WR</span>
                <span style="color:#6b7280;font-size:0.8rem">${{lists.length}} lists | ${{arch.deckNames.join(', ')}}</span>
            </div>
            <div class="deck-cards">`;
        if (lists.length === 0) {{
            html += '<p style="color:#6b7280;padding:1rem">No hay decklists descargadas para este arquetipo.</p>';
        }} else {{
            html += '<div class="card-list">';
            lists.forEach(dl => {{
                html += `<div class="deck-card"><h4>${{dl.name}}</h4><div class="player">${{dl.player}}</div>`;
                const main = dl.cards.filter(c => c.component === 'main');
                const side = dl.cards.filter(c => c.component === 'sideboard');
                const comp = dl.cards.filter(c => c.component === 'companion');
                if (main.length) {{
                    html += '<div class="card-section-title">Main Deck (' + main.reduce((s,c) => s+c.qty, 0) + ')</div>';
                    main.forEach(c => {{ html += `<div class="card-entry"><span class="qty">${{c.qty}}</span>${{c.name}}</div>`; }});
                }}
                if (side.length) {{
                    html += '<div class="card-section-title">Sideboard (' + side.reduce((s,c) => s+c.qty, 0) + ')</div>';
                    side.forEach(c => {{ html += `<div class="card-entry"><span class="qty">${{c.qty}}</span>${{c.name}}</div>`; }});
                }}
                if (comp.length) {{
                    html += '<div class="card-section-title">Companion</div>';
                    comp.forEach(c => {{ html += `<div class="card-entry"><span class="qty">${{c.qty}}</span>${{c.name}}</div>`; }});
                }}
                html += '</div>';
            }});
            html += '</div>';
        }}
        html += '</div></div>';
    }});
    container.innerHTML = html;
}}
document.getElementById('deck-search').addEventListener('input', renderDecklists);
renderDecklists();
</script>
</body>
</html>"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[*] Dashboard HTML generado: {filepath}")


# ---------------------------------------------------------------------------
# Exportar todo a JSON
# ---------------------------------------------------------------------------
def export_full_json(meta: MetagameData, tournament: scraper.TournamentData, filepath: str):
    """Exporta todos los datos del análisis a JSON."""
    output = {
        "tournament": {
            "id": tournament.tournament_id,
            "name": tournament.tournament_name,
            "rounds": len(tournament.rounds),
            "total_matches": meta.total_matches,
            "total_players": meta.total_players,
        },
        "archetypes": [
            {
                "name": arch.name,
                "meta_share": round(meta.meta_share.get(arch.name, 0), 2),
                "pilots": arch.count,
                "wins": arch.wins,
                "losses": arch.losses,
                "draws": arch.draws,
                "winrate": round(arch.winrate, 2),
                "deck_names": arch.deck_names,
                "sample_decklists": [
                    {
                        "name": dl.name,
                        "player": dl.player_name,
                        "cards": [
                            {"name": c.name, "qty": c.quantity, "type": c.card_type, "component": c.component}
                            for c in dl.cards
                        ],
                    }
                    for dl in arch.decklists[:5]
                ],
            }
            for arch in sorted(meta.archetypes.values(), key=lambda a: a.count, reverse=True)
        ],
        "matchup_matrix": {
            a1: {
                a2: stats for a2, stats in inner.items()
            }
            for a1, inner in meta.matchup_matrix.items()
        },
        "deck_to_archetype": meta.deck_to_archetype,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[*] JSON completo exportado a: {filepath}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="MTG Meta Analyzer para melee.gg")
    parser.add_argument("-t", "--tournament", default=scraper.DEFAULT_TOURNAMENT_ID, help="ID del torneo")
    parser.add_argument("--skip-decklists", action="store_true", help="No descargar cartas de decklists")
    parser.add_argument("--max-decklists", type=int, default=0, help="Máximo de decklists a descargar (0=todas)")
    parser.add_argument("--min-matches", type=int, default=5, help="Mínimo de partidas para matriz")
    parser.add_argument("--html", default="meta_analyzer.html", help="Archivo HTML de salida")
    parser.add_argument("--json-out", default="meta_analysis.json", help="Archivo JSON de salida")
    args = parser.parse_args()

    # 1. Scrape tournament
    tournament = scraper.scrape_tournament(args.tournament)

    # 2. Build archetype map
    archetype_map = build_archetype_map(tournament)
    print(f"\n[*] Arquetipos identificados: {len(set(archetype_map.values()))}")
    for arch, count in Counter(archetype_map.values()).most_common(20):
        print(f"  {arch}: {count} deck names")

    # 3. Download decklists (optional)
    decklists = []
    if not args.skip_decklists:
        deck_ids = extract_decklist_ids_from_matches(tournament)
        decklists = download_all_decklists(deck_ids, max_decks=args.max_decklists)
        export_decklists_csv(decklists, "decklists.csv")

    # 4. Analyze metagame
    meta = analyze_metagame(tournament, archetype_map, decklists)

    # 5. Export
    export_archetype_summary_csv(meta, "archetype_summary.csv")
    generate_html_dashboard(meta, tournament, args.html)
    export_full_json(meta, tournament, args.json_out)

    # 6. Print summary
    print(f"\n{'='*60}")
    print("RESUMEN DEL METAGAME")
    print(f"{'='*60}")
    print(f"{'Archetype':<30} {'Share':>6} {'Pilots':>7} {'WR%':>6}")
    print("-" * 55)
    for arch in sorted(meta.archetypes.values(), key=lambda a: a.count, reverse=True)[:20]:
        share = meta.meta_share.get(arch.name, 0)
        print(f"{arch.name:<30} {share:>5.1f}% {arch.count:>6} {arch.winrate:>5.1f}%")


if __name__ == "__main__":
    main()
