#!/usr/bin/env python3
"""
Web scraper para melee.gg — extrae matchups de torneos sin credenciales.

Funciona haciendo scraping directo del HTML (para obtener round IDs)
y luego llamando al endpoint interno /Match/GetRoundMatches/{roundId}
con formato DataTables server-side POST.

Uso:
    python melee_scraper.py                           # torneo por defecto (339227)
    python melee_scraper.py --tournament 339227       # otro torneo
    python melee_scraper.py --output datos.csv        # exportar CSV
"""

import argparse
import csv
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
BASE_URL = "https://melee.gg"
DEFAULT_TOURNAMENT_ID = "339227"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
REQUEST_DELAY = 0.5  # segundos entre requests para no saturar el servidor


# ---------------------------------------------------------------------------
# Modelos de datos
# ---------------------------------------------------------------------------
@dataclass
class Player:
    id: int
    display_name: str
    username: str
    team_id: int


@dataclass
class MatchResult:
    round_number: int
    round_id: int
    player1: Player
    player2: Optional[Player]  # None si es bye
    player1_wins: int
    player2_wins: int
    draws: int
    is_bye: bool
    bye_reason: Optional[str]
    result_string: str
    player1_decklist: Optional[str] = None
    player2_decklist: Optional[str] = None
    format_name: Optional[str] = None


@dataclass
class TournamentData:
    tournament_id: str
    tournament_name: str
    rounds: dict = field(default_factory=dict)  # {round_id: round_name}
    matches: list = field(default_factory=list)  # [MatchResult, ...]
    players: dict = field(default_factory=dict)  # {player_id: Player}


# ---------------------------------------------------------------------------
# Funciones de red
# ---------------------------------------------------------------------------
def _ssl_context():
    return ssl.create_default_context()


def _get(url: str, ctx: ssl.SSLContext, accept: str = "text/html") -> str:
    """GET request sencillo."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": accept,
    })
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _post_datatables(
    url: str,
    ctx: ssl.SSLContext,
    referer: str,
    start: int = 0,
    length: int = 500,
) -> dict:
    """
    POST con formato DataTables server-side para obtener datos paginados.
    Devuelve el JSON parseado.
    """
    params = {
        "draw": "1",
        "start": str(start),
        "length": str(length),
        "search[value]": "",
        "search[regex]": "false",
        "order[0][column]": "0",
        "order[0][dir]": "asc",
    }
    # Columnas que espera el endpoint de pairings
    columns = [
        ("TableNumber", "true", "true"),
        ("PodNumber", "true", "true"),
        ("Teams", "false", "false"),
        ("Decklists", "false", "false"),
        ("ResultString", "false", "false"),
    ]
    for i, (data, searchable, orderable) in enumerate(columns):
        params[f"columns[{i}][data]"] = data
        params[f"columns[{i}][name]"] = ""
        params[f"columns[{i}][searchable]"] = searchable
        params[f"columns[{i}][orderable]"] = orderable
        params[f"columns[{i}][search][value]"] = ""
        params[f"columns[{i}][search][regex]"] = "false"

    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
        "Origin": BASE_URL,
    })
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    return json.loads(text)


# ---------------------------------------------------------------------------
# Extracción de datos
# ---------------------------------------------------------------------------
def fetch_tournament_page(tournament_id: str, ctx: ssl.SSLContext) -> str:
    """Descarga la página HTML del torneo."""
    url = f"{BASE_URL}/Tournament/View/{tournament_id}"
    print(f"[*] Descargando página del torneo {tournament_id}...")
    return _get(url, ctx)


def extract_round_ids(html: str) -> list[tuple[str, str]]:
    """
    Extrae (round_id, round_name) de los botones round-selector
    dentro del contenedor pairings-round-selector-container.
    """
    # Encontrar el bloque del contenedor de pairings
    pairings_idx = html.find("pairings-round-selector-container")
    if pairings_idx < 0:
        # Fallback: buscar en todo el HTML
        pattern = r'data-id="(\d+)"\s+data-name="([^"]+)"\s+data-is-started="True"'
        return re.findall(pattern, html)

    # Extraer solo la sección de pairings (evitar duplicados con standings)
    section_end = html.find("</div>", pairings_idx + 500)
    if section_end < 0:
        section_end = pairings_idx + 5000
    section = html[pairings_idx:section_end + 200]

    pattern = r'data-id="(\d+)"\s+data-name="([^"]+)"'
    return re.findall(pattern, section)


def extract_tournament_name(html: str) -> str:
    """Extrae el nombre del torneo del <title>."""
    m = re.search(r"<title>([^<]+)</title>", html, re.I)
    if m:
        name = m.group(1).replace(" | Melee", "").strip()
        return name
    return "Unknown Tournament"


def fetch_round_matches(
    round_id: str,
    tournament_id: str,
    ctx: ssl.SSLContext,
) -> list[dict]:
    """
    Descarga TODOS los matches de una ronda usando paginación DataTables.
    """
    url = f"{BASE_URL}/Match/GetRoundMatches/{round_id}"
    referer = f"{BASE_URL}/Tournament/View/{tournament_id}"
    all_matches = []
    start = 0
    page_size = 500

    while True:
        data = _post_datatables(url, ctx, referer, start=start, length=page_size)
        records = data.get("data", [])
        total = data.get("recordsTotal", 0)
        all_matches.extend(records)

        if len(all_matches) >= total or not records:
            break
        start += page_size
        time.sleep(REQUEST_DELAY)

    return all_matches


def parse_player_from_competitor(comp: dict) -> Optional[Player]:
    """Extrae un Player de un objeto Competitor del JSON."""
    team = comp.get("Team", {})
    players = team.get("Players", [])
    if not players:
        return None
    p = players[0]
    return Player(
        id=p.get("ID", 0),
        display_name=p.get("DisplayName", "Unknown"),
        username=p.get("Username", ""),
        team_id=team.get("ID", 0),
    )


def parse_match(match_json: dict, round_number: int, round_id: int) -> MatchResult:
    """Convierte un objeto match del JSON en un MatchResult."""
    competitors = match_json.get("Competitors", [])
    is_bye = match_json.get("ByeReason") is not None or len(competitors) < 2

    player1 = parse_player_from_competitor(competitors[0]) if competitors else None
    player2 = parse_player_from_competitor(competitors[1]) if len(competitors) > 1 else None

    # Extraer wins
    p1_wins = 0
    p2_wins = 0
    if competitors:
        p1_wins = competitors[0].get("GameWinsAndGameByes", 0) or 0
    if len(competitors) > 1:
        p2_wins = competitors[1].get("GameWinsAndGameByes", 0) or 0

    # Decklists
    p1_deck = None
    p2_deck = None
    if competitors and competitors[0].get("Decklists"):
        p1_deck = competitors[0]["Decklists"][0].get("DecklistName")
    if len(competitors) > 1 and competitors[1].get("Decklists"):
        p2_deck = competitors[1]["Decklists"][0].get("DecklistName")

    return MatchResult(
        round_number=round_number,
        round_id=round_id,
        player1=player1,
        player2=player2,
        player1_wins=p1_wins,
        player2_wins=p2_wins,
        draws=match_json.get("GameDraws", 0),
        is_bye=is_bye,
        bye_reason=match_json.get("ByeReasonDescription"),
        result_string=match_json.get("ResultString", ""),
        player1_decklist=p1_deck,
        player2_decklist=p2_deck,
        format_name=match_json.get("Format"),
    )


# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------
def scrape_tournament(tournament_id: str) -> TournamentData:
    """
    Scraper completo: descarga página, extrae rondas, y obtiene todos los matches.
    """
    ctx = _ssl_context()

    # 1. Descargar página HTML
    html = fetch_tournament_page(tournament_id, ctx)
    tournament_name = extract_tournament_name(html)
    print(f"[*] Torneo: {tournament_name}")

    # 2. Extraer round IDs
    round_info = extract_round_ids(html)
    if not round_info:
        print("[!] No se encontraron rondas. ¿El torneo tiene pairings publicados?")
        sys.exit(1)

    print(f"[*] Rondas encontradas: {len(round_info)}")

    tournament = TournamentData(
        tournament_id=tournament_id,
        tournament_name=tournament_name,
    )

    # 3. Descargar matches de cada ronda
    for round_id, round_name in round_info:
        tournament.rounds[round_id] = round_name
        print(f"  [>] {round_name} (id={round_id})...", end=" ", flush=True)

        try:
            raw_matches = fetch_round_matches(round_id, tournament_id, ctx)
            round_number = int(re.search(r"\d+", round_name).group()) if re.search(r"\d+", round_name) else 0

            for m in raw_matches:
                match = parse_match(m, round_number, int(round_id))
                tournament.matches.append(match)

                # Registrar jugadores
                if match.player1:
                    tournament.players[match.player1.id] = match.player1
                if match.player2:
                    tournament.players[match.player2.id] = match.player2

            print(f"{len(raw_matches)} matches")
        except Exception as e:
            print(f"ERROR: {e}")

        time.sleep(REQUEST_DELAY)

    print(f"\n[*] Total: {len(tournament.matches)} matches, {len(tournament.players)} jugadores")
    return tournament


# ---------------------------------------------------------------------------
# Generación de matriz de emparejamientos
# ---------------------------------------------------------------------------
def build_matchup_matrix(tournament: TournamentData) -> dict:
    """
    Construye una matriz de emparejamientos deck vs deck.
    Retorna:
        {
            'decks': [lista de nombres de decks],
            'matrix': {deck1: {deck2: {'wins': W, 'losses': L, 'draws': D, 'total': T}}},
            'player_matches': [lista de dicts con detalle de cada match],
        }
    """
    deck_matchups = defaultdict(lambda: defaultdict(lambda: {"wins": 0, "losses": 0, "draws": 0, "total": 0}))
    player_matches = []

    for match in tournament.matches:
        if match.is_bye or not match.player1 or not match.player2:
            continue
        if not match.player1_decklist or not match.player2_decklist:
            continue

        d1 = match.player1_decklist
        d2 = match.player2_decklist

        # Determinar ganador
        if match.player1_wins > match.player2_wins:
            deck_matchups[d1][d2]["wins"] += 1
            deck_matchups[d2][d1]["losses"] += 1
            winner = match.player1.display_name
        elif match.player2_wins > match.player1_wins:
            deck_matchups[d1][d2]["losses"] += 1
            deck_matchups[d2][d1]["wins"] += 1
            winner = match.player2.display_name
        else:
            deck_matchups[d1][d2]["draws"] += 1
            deck_matchups[d2][d1]["draws"] += 1
            winner = "Draw"

        deck_matchups[d1][d2]["total"] += 1
        deck_matchups[d2][d1]["total"] += 1

        player_matches.append({
            "round": match.round_number,
            "player1": match.player1.display_name,
            "player1_deck": d1,
            "player1_wins": match.player1_wins,
            "player2": match.player2.display_name,
            "player2_deck": d2,
            "player2_wins": match.player2_wins,
            "draws": match.draws,
            "winner": winner,
            "result": match.result_string,
        })

    all_decks = sorted(set(
        list(deck_matchups.keys()) +
        [d for inner in deck_matchups.values() for d in inner.keys()]
    ))

    return {
        "decks": all_decks,
        "matrix": dict(deck_matchups),
        "player_matches": player_matches,
    }


def print_matchup_matrix(matrix_data: dict):
    """Imprime la matriz de emparejamientos en formato legible."""
    decks = matrix_data["decks"]
    matrix = matrix_data["matrix"]

    if not decks:
        print("[!] No hay datos de decklists disponibles para construir la matriz.")
        return

    print(f"\n{'='*80}")
    print("MATRIZ DE EMPAREJAMIENTOS (Win-Loss-Draw)")
    print(f"{'='*80}\n")

    # Calcular metadatos por deck
    deck_stats = {}
    for d in decks:
        total_w = sum(matrix.get(d, {}).get(d2, {}).get("wins", 0) for d2 in decks)
        total_l = sum(matrix.get(d, {}).get(d2, {}).get("losses", 0) for d2 in decks)
        total_d = sum(matrix.get(d, {}).get(d2, {}).get("draws", 0) for d2 in decks)
        total = total_w + total_l + total_d
        winrate = total_w / total * 100 if total > 0 else 0
        deck_stats[d] = {"wins": total_w, "losses": total_l, "draws": total_d, "total": total, "winrate": winrate}

    # Ordenar por winrate descendente
    decks_sorted = sorted(decks, key=lambda d: deck_stats[d]["winrate"], reverse=True)

    # Imprimir resumen
    print(f"{'Deck':<35} {'W':>4} {'L':>4} {'D':>4} {'Total':>6} {'Win%':>7}")
    print("-" * 65)
    for d in decks_sorted:
        s = deck_stats[d]
        if s["total"] > 0:
            print(f"{d:<35} {s['wins']:>4} {s['losses']:>4} {s['draws']:>4} {s['total']:>6} {s['winrate']:>6.1f}%")

    # Imprimir matriz detallada para los decks con más de N partidas
    MIN_MATCHES = 5
    popular_decks = [d for d in decks_sorted if deck_stats[d]["total"] >= MIN_MATCHES]
    if popular_decks:
        print(f"\n{'='*80}")
        print(f"MATRIZ DETALLADA (decks con >= {MIN_MATCHES} partidas)")
        print(f"{'='*80}\n")

        header = f"{'VS':<25}"
        for d2 in popular_decks:
            short = d2[:12]
            header += f" {short:>13}"
        print(header)
        print("-" * len(header))

        for d1 in popular_decks:
            row = f"{d1[:24]:<25}"
            for d2 in popular_decks:
                if d1 == d2:
                    row += f" {'mirror':>13}"
                else:
                    stats = matrix.get(d1, {}).get(d2, {"wins": 0, "losses": 0, "draws": 0})
                    w, l, dr = stats["wins"], stats["losses"], stats["draws"]
                    total = w + l + dr
                    if total > 0:
                        wr = w / total * 100
                        row += f" {w}-{l}-{dr} ({wr:.0f}%)"
                        # Pad to 13
                        cell = f"{w}-{l}-{dr} ({wr:.0f}%)"
                        row = row[:-len(cell)] + f"{cell:>13}"
                    else:
                        row += f" {'--':>13}"
            print(row)


# ---------------------------------------------------------------------------
# Exportación
# ---------------------------------------------------------------------------
def export_matches_csv(tournament: TournamentData, filepath: str):
    """Exporta todos los matches a un CSV."""
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Round", "Player1", "Player1_Deck", "Player1_Wins",
            "Player2", "Player2_Deck", "Player2_Wins",
            "Draws", "IsBye", "Result",
        ])
        for m in tournament.matches:
            writer.writerow([
                m.round_number,
                m.player1.display_name if m.player1 else "",
                m.player1_decklist or "",
                m.player1_wins,
                m.player2.display_name if m.player2 else "",
                m.player2_decklist or "",
                m.player2_wins,
                m.draws,
                m.is_bye,
                m.result_string,
            ])
    print(f"[*] Matches exportados a: {filepath}")


def export_matrix_csv(matrix_data: dict, filepath: str):
    """Exporta la matriz de emparejamientos a un CSV."""
    decks = matrix_data["decks"]
    matrix = matrix_data["matrix"]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Header
        writer.writerow(["Deck vs Deck"] + decks)
        for d1 in decks:
            row = [d1]
            for d2 in decks:
                if d1 == d2:
                    row.append("mirror")
                else:
                    stats = matrix.get(d1, {}).get(d2, {})
                    w = stats.get("wins", 0)
                    l = stats.get("losses", 0)
                    d = stats.get("draws", 0)
                    total = w + l + d
                    if total > 0:
                        wr = w / total * 100
                        row.append(f"{w}-{l}-{d} ({wr:.0f}%)")
                    else:
                        row.append("")
            writer.writerow(row)
    print(f"[*] Matriz exportada a: {filepath}")


def export_json(tournament: TournamentData, matrix_data: dict, filepath: str):
    """Exporta todo a JSON."""
    output = {
        "tournament": {
            "id": tournament.tournament_id,
            "name": tournament.tournament_name,
            "total_rounds": len(tournament.rounds),
            "total_matches": len(tournament.matches),
            "total_players": len(tournament.players),
        },
        "matchup_matrix": {
            "decks": matrix_data["decks"],
            "matrix": {
                d1: {d2: stats for d2, stats in inner.items()}
                for d1, inner in matrix_data["matrix"].items()
            },
        },
        "matches": matrix_data["player_matches"],
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[*] Datos completos exportados a: {filepath}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Scraper de melee.gg para matrices de emparejamientos"
    )
    parser.add_argument(
        "--tournament", "-t",
        default=DEFAULT_TOURNAMENT_ID,
        help=f"ID del torneo (default: {DEFAULT_TOURNAMENT_ID})",
    )
    parser.add_argument(
        "--output", "-o",
        default="matches.csv",
        help="Archivo CSV de salida para matches (default: matches.csv)",
    )
    parser.add_argument(
        "--matrix-csv",
        default="matchup_matrix.csv",
        help="Archivo CSV de salida para la matriz (default: matchup_matrix.csv)",
    )
    parser.add_argument(
        "--json",
        default="tournament_data.json",
        help="Archivo JSON de salida (default: tournament_data.json)",
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="No exportar archivos, solo mostrar en consola",
    )

    args = parser.parse_args()

    # Scrape
    tournament = scrape_tournament(args.tournament)

    # Construir matriz
    matrix_data = build_matchup_matrix(tournament)
    print_matchup_matrix(matrix_data)

    # Exportar
    if not args.no_export:
        export_matches_csv(tournament, args.output)
        export_matrix_csv(matrix_data, args.matrix_csv)
        export_json(tournament, matrix_data, args.json)


if __name__ == "__main__":
    main()
