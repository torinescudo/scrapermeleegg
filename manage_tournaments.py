#!/usr/bin/env python3
"""
Gestor de base de datos de torneos.

Almacena torneos scrapeados en tournaments_db.json para que el sitio
generado los incluya todos como seleccionables.

Uso:
    python manage_tournaments.py add https://melee.gg/Tournament/View/339227
    python manage_tournaments.py add 339227
    python manage_tournaments.py list
    python manage_tournaments.py remove 339227
    python manage_tournaments.py generate          # regenera el sitio

Uso programático:
    from manage_tournaments import TournamentDB
    db = TournamentDB()
    db.add_tournament("https://melee.gg/Tournament/View/339227")
    db.generate_site()
"""

import argparse
import json
import os
import re
import sys
import time

# Importar módulos del proyecto
import melee_scraper as scraper


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tournaments_db.json")
DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")


def extract_tournament_id(url_or_id: str) -> str:
    """
    Extrae el ID del torneo de una URL de melee.gg o lo retorna tal cual si ya es un ID.
    
    Acepta:
        - https://melee.gg/Tournament/View/339227
        - melee.gg/Tournament/View/339227
        - 339227
    """
    url_or_id = url_or_id.strip()
    # Buscar patrón de URL
    m = re.search(r'Tournament/View/(\d+)', url_or_id, re.I)
    if m:
        return m.group(1)
    # Si es solo dígitos, es el ID directo
    if url_or_id.isdigit():
        return url_or_id
    raise ValueError(
        f"No se pudo extraer un ID de torneo de: '{url_or_id}'\n"
        f"Formatos válidos:\n"
        f"  - https://melee.gg/Tournament/View/339227\n"
        f"  - 339227"
    )


class TournamentDB:
    """Base de datos simple de torneos en un archivo JSON."""

    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self.data = self._load()

    def _load(self) -> dict:
        """Carga la DB desde disco, o crea una vacía."""
        if os.path.exists(self.db_path):
            with open(self.db_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"tournaments": {}}

    def _save(self):
        """Guarda la DB a disco."""
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    @property
    def tournaments(self) -> dict:
        return self.data.get("tournaments", {})

    def has_tournament(self, tournament_id: str) -> bool:
        return tournament_id in self.tournaments

    def get_tournament(self, tournament_id: str) -> dict | None:
        return self.tournaments.get(tournament_id)

    def list_tournaments(self) -> list[dict]:
        """Retorna lista de resúmenes de torneos almacenados."""
        result = []
        for tid, tdata in self.tournaments.items():
            info = tdata.get("tournament", {})
            result.append({
                "id": tid,
                "name": info.get("name", "Unknown"),
                "total_rounds": info.get("total_rounds", 0),
                "total_matches": info.get("total_matches", 0),
                "total_players": info.get("total_players", 0),
                "scraped_at": tdata.get("scraped_at", "unknown"),
            })
        return result

    def add_tournament(self, url_or_id: str, force: bool = False) -> dict:
        """
        Agrega un torneo a la DB. Si ya existe, lo retorna sin re-scrapear
        (a menos que force=True).
        
        Args:
            url_or_id: URL de melee.gg o ID numérico del torneo
            force: Si True, re-scrapea aunque ya exista
            
        Returns:
            dict con los datos del torneo
        """
        tournament_id = extract_tournament_id(url_or_id)
        
        if self.has_tournament(tournament_id) and not force:
            print(f"[*] Torneo {tournament_id} ya está en la base de datos.")
            info = self.tournaments[tournament_id]["tournament"]
            print(f"    Nombre: {info.get('name', '?')}")
            print(f"    Matches: {info.get('total_matches', '?')}")
            print(f"    Scrapeado: {self.tournaments[tournament_id].get('scraped_at', '?')}")
            print(f"    Usa force=True para re-scrapear.")
            return self.tournaments[tournament_id]

        # Scrapear el torneo
        print(f"[*] Scrapeando torneo {tournament_id}...")
        tournament = scraper.scrape_tournament(tournament_id)
        matrix_data = scraper.build_matchup_matrix(tournament)

        # Construir datos para guardar (mismo formato que tournament_data.json)
        tournament_data = {
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
            "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }

        # Guardar en la DB
        self.data["tournaments"][tournament_id] = tournament_data
        self._save()

        info = tournament_data["tournament"]
        print(f"\n[✓] Torneo agregado a la base de datos:")
        print(f"    ID: {tournament_id}")
        print(f"    Nombre: {info['name']}")
        print(f"    Rondas: {info['total_rounds']}")
        print(f"    Matches: {info['total_matches']}")
        print(f"    Jugadores: {info['total_players']}")

        return tournament_data

    def remove_tournament(self, url_or_id: str) -> bool:
        """Elimina un torneo de la DB."""
        tournament_id = extract_tournament_id(url_or_id)
        if tournament_id in self.data["tournaments"]:
            name = self.data["tournaments"][tournament_id]["tournament"].get("name", "?")
            del self.data["tournaments"][tournament_id]
            self._save()
            print(f"[✓] Torneo {tournament_id} ({name}) eliminado.")
            return True
        print(f"[!] Torneo {tournament_id} no encontrado en la base de datos.")
        return False

    def import_from_json(self, filepath: str) -> str | None:
        """
        Importa un tournament_data.json existente a la DB.
        Retorna el tournament_id importado o None si falla.
        """
        if not os.path.exists(filepath):
            print(f"[!] Archivo no encontrado: {filepath}")
            return None

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        tournament_id = str(data["tournament"]["id"])
        data["scraped_at"] = data.get("scraped_at", time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))

        self.data["tournaments"][tournament_id] = data
        self._save()

        print(f"[✓] Importado: {data['tournament']['name']} (ID: {tournament_id})")
        return tournament_id

    def generate_site(self, output_dir: str = DIST_DIR):
        """Genera el sitio estático con todos los torneos de la DB."""
        import generate_site
        generate_site.generate_multi_tournament_site(self.data["tournaments"], output_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Gestor de base de datos de torneos de melee.gg"
    )
    sub = parser.add_subparsers(dest="command", help="Comando a ejecutar")

    # add
    add_p = sub.add_parser("add", help="Agregar un torneo (scrapea si es nuevo)")
    add_p.add_argument("url", help="URL de melee.gg o ID del torneo")
    add_p.add_argument("--force", "-f", action="store_true", help="Re-scrapear aunque ya exista")

    # list
    sub.add_parser("list", help="Listar torneos en la base de datos")

    # remove
    rm_p = sub.add_parser("remove", help="Eliminar un torneo de la base de datos")
    rm_p.add_argument("url", help="URL o ID del torneo a eliminar")

    # import
    imp_p = sub.add_parser("import", help="Importar un tournament_data.json existente")
    imp_p.add_argument("file", help="Archivo JSON a importar")

    # generate
    gen_p = sub.add_parser("generate", help="Regenerar el sitio estático")
    gen_p.add_argument("--output", "-o", default="dist", help="Directorio de salida")

    args = parser.parse_args()
    db = TournamentDB()

    if args.command == "add":
        db.add_tournament(args.url, force=args.force)
    elif args.command == "list":
        tournaments = db.list_tournaments()
        if not tournaments:
            print("[*] No hay torneos en la base de datos.")
            print("    Usa: python manage_tournaments.py add <url>")
        else:
            print(f"[*] {len(tournaments)} torneo(s) en la base de datos:\n")
            for t in tournaments:
                print(f"  ID: {t['id']}")
                print(f"  Nombre: {t['name']}")
                print(f"  Matches: {t['total_matches']} | Jugadores: {t['total_players']} | Rondas: {t['total_rounds']}")
                print(f"  Scrapeado: {t['scraped_at']}")
                print()
    elif args.command == "remove":
        db.remove_tournament(args.url)
    elif args.command == "import":
        db.import_from_json(args.file)
    elif args.command == "generate":
        if not db.tournaments:
            print("[!] No hay torneos. Agrega uno primero con: add <url>")
            sys.exit(1)
        db.generate_site(args.output)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
