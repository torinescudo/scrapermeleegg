#!/usr/bin/env python3
"""
Servidor local para MTG Meta Analyzer.

Sirve el sitio estático y expone una API para gestionar torneos
directamente desde el navegador.

Uso:
    python server.py              # Puerto 8080 por defecto
    python server.py --port 3000  # Puerto personalizado

Endpoints API:
    GET  /api/tournaments          Lista de torneos
    POST /api/tournaments          Añadir torneo  {url: "https://melee.gg/..."}
    DELETE /api/tournaments/<id>   Eliminar torneo
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import manage_tournaments as mt
import generate_site

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, "dist")


class MetaAnalyzerHandler(SimpleHTTPRequestHandler):
    """HTTP handler: sirve archivos estáticos de dist/ + API REST."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIST_DIR, **kwargs)

    # ── Helpers ──────────────────────────────────────────────
    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw)

    # ── CORS preflight ───────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET ───────────────────────────────────────────────────
    def do_GET(self):
        if self.path == "/api/tournaments":
            db = mt.TournamentDB()
            self._send_json({
                "ok": True,
                "tournaments": db.list_tournaments(),
                "count": len(db.tournaments),
            })
            return

        if self.path == "/api/status":
            self._send_json({"ok": True, "status": "running"})
            return

        # Servir archivos estáticos (sin caché)
        super().do_GET()

    def end_headers(self):
        """Añadir headers anti-caché a TODAS las respuestas."""
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    # ── POST ──────────────────────────────────────────────────
    def do_POST(self):
        if self.path == "/api/tournaments":
            try:
                body = self._read_body()
                url = body.get("url", "").strip()
                force = body.get("force", False)

                if not url:
                    self._send_json({"ok": False, "error": "Falta el campo 'url'"}, 400)
                    return

                # Extraer ID
                try:
                    tid = mt.extract_tournament_id(url)
                except ValueError as e:
                    self._send_json({"ok": False, "error": str(e)}, 400)
                    return

                db = mt.TournamentDB()

                # Comprobar si ya existe
                if db.has_tournament(tid) and not force:
                    t = db.get_tournament(tid)
                    info = t["tournament"]
                    self._send_json({
                        "ok": True,
                        "action": "already_exists",
                        "tournament_id": tid,
                        "name": info["name"],
                        "message": f"El torneo '{info['name']}' ya está en la base de datos.",
                    })
                    return

                # Scrapear en un thread para no bloquear
                # Pero como necesitamos responder sincrónicamente, lo hacemos inline
                self._send_json({
                    "ok": True,
                    "action": "scraping",
                    "tournament_id": tid,
                    "message": f"Scrapeando torneo {tid}... esto puede tardar 1-2 minutos.",
                })
                # No podemos hacer streaming fácil con stdlib,
                # así que usamos /api/tournaments/add-sync
                return

            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
                return

        if self.path == "/api/tournaments/add-sync":
            try:
                body = self._read_body()
                url = body.get("url", "").strip()
                force = body.get("force", False)

                if not url:
                    self._send_json({"ok": False, "error": "Falta el campo 'url'"}, 400)
                    return

                tid = mt.extract_tournament_id(url)
                db = mt.TournamentDB()

                # Comprobar si ya existe
                if db.has_tournament(tid) and not force:
                    t = db.get_tournament(tid)
                    info = t["tournament"]
                    self._send_json({
                        "ok": True,
                        "action": "already_exists",
                        "tournament_id": tid,
                        "name": info["name"],
                        "total_matches": info.get("total_matches", 0),
                        "message": f"Torneo ya existente: {info['name']}",
                    })
                    # Regenerar sitio de todas formas (por si no estaba generado)
                    self._regenerate_site(db)
                    return

                # Scrapear (puede tardar)
                tdata = db.add_tournament(url, force=force)
                info = tdata["tournament"]

                # Regenerar sitio
                self._regenerate_site(db)

                self._send_json({
                    "ok": True,
                    "action": "added",
                    "tournament_id": tid,
                    "name": info["name"],
                    "total_matches": info.get("total_matches", 0),
                    "total_players": info.get("total_players", 0),
                    "total_rounds": info.get("total_rounds", 0),
                    "message": f"Torneo añadido: {info['name']}",
                })

            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send_json({"ok": False, "error": str(e)}, 500)
            return

        self._send_json({"ok": False, "error": "Endpoint no encontrado"}, 404)

    # ── DELETE ────────────────────────────────────────────────
    def do_DELETE(self):
        # /api/tournaments/339227
        if self.path.startswith("/api/tournaments/"):
            tid = self.path.split("/")[-1]
            db = mt.TournamentDB()
            if db.has_tournament(tid):
                name = db.get_tournament(tid)["tournament"]["name"]
                db.remove_tournament(tid)
                self._regenerate_site(db)
                self._send_json({
                    "ok": True,
                    "action": "removed",
                    "tournament_id": tid,
                    "name": name,
                    "message": f"Torneo eliminado: {name}",
                })
            else:
                self._send_json({"ok": False, "error": f"Torneo {tid} no encontrado"}, 404)
            return

        self._send_json({"ok": False, "error": "Endpoint no encontrado"}, 404)

    # ── Regenerar sitio ──────────────────────────────────────
    def _regenerate_site(self, db):
        """Regenera el sitio estático con todos los torneos actuales."""
        try:
            import importlib
            importlib.reload(generate_site)
            if db.tournaments:
                generate_site.generate_multi_tournament_site(db.data["tournaments"], DIST_DIR)
                print("[✓] Sitio regenerado.")
            else:
                print("[!] Sin torneos — sitio no regenerado.")
        except Exception as e:
            print(f"[!] Error regenerando sitio: {e}")

    # ── Log todas las requests ─────────────────────────────
    def log_message(self, format, *args):
        # Log everything for debugging
        super().log_message(format, *args)


def run_server(port=8080):
    """Arranca el servidor HTTP."""
    # Asegurar que dist/ existe
    if not os.path.exists(DIST_DIR):
        print("[*] dist/ no existe, generando sitio...")
        db = mt.TournamentDB()
        if db.tournaments:
            generate_site.generate_multi_tournament_site(db.data["tournaments"], DIST_DIR)
        else:
            os.makedirs(DIST_DIR, exist_ok=True)
            with open(os.path.join(DIST_DIR, "index.html"), "w") as f:
                f.write("<html><body><h1>No hay torneos. Usa la API para añadir uno.</h1></body></html>")

    server = HTTPServer(("0.0.0.0", port), MetaAnalyzerHandler)
    print(f"")
    print(f"  🎴  MTG Meta Analyzer Server")
    print(f"  ════════════════════════════════════")
    print(f"  🌐  http://localhost:{port}")
    print(f"  📁  Sirviendo: {DIST_DIR}")
    print(f"")
    print(f"  API Endpoints:")
    print(f"    GET    /api/tournaments          → listar torneos")
    print(f"    POST   /api/tournaments/add-sync → añadir torneo")
    print(f"    DELETE /api/tournaments/<id>      → eliminar torneo")
    print(f"")
    print(f"  Ctrl+C para detener")
    print(f"")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Servidor detenido.")
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MTG Meta Analyzer Server")
    parser.add_argument("--port", "-p", type=int, default=8080, help="Puerto (default: 8080)")
    args = parser.parse_args()
    run_server(args.port)
