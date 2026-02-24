# 🎴 MTG Meta Analyzer — Scraper melee.gg

Scraper de torneos de [melee.gg](https://melee.gg) que extrae resultados de partidas, construye una **matriz de emparejamientos** (matchup matrix) y genera un **dashboard HTML estático** desplegable en Netlify.

## Características

- **Scraping sin credenciales** — usa endpoints DataTables internos de melee.gg
- **Matriz de matchups** — win/loss/draw por arquetipo vs arquetipo  
- **Dashboard interactivo** — HTML estático autocontenido con:
  - Metagame breakdown (pie chart + tabla con sorting/filtrado)
  - Matchup matrix con heatmap y tooltips
  - Deck detail con barras de matchup favorable/desfavorable
- **Deploy en Netlify** con un solo push

## Estructura del proyecto

```
melee_scraper.py         # Scraper base — extrae matches de melee.gg
meta_analyzer.py         # Análisis avanzado con descarga de decklists
generate_site.py         # Genera dist/index.html (dashboard estático)
netlify.toml             # Config de deploy para Netlify
melee_research.ipynb     # Notebook de investigación y desarrollo
tournament_data.json     # Datos del último torneo scrapeado
matches.csv              # Matches exportados en CSV
matchup_matrix.csv       # Matriz de matchups en CSV
```

## Uso rápido

### 1. Scrapear un torneo

```bash
python melee_scraper.py --tournament 339227
```

Esto genera `tournament_data.json`, `matches.csv` y `matchup_matrix.csv`.

### 2. Generar el sitio

```bash
python generate_site.py
```

Genera `dist/index.html` (~725 KB, todo embebido).

### 3. Preview local

```bash
python -m http.server -d dist 8080
```

### 4. Deploy a Netlify

Simplemente haz push a la rama `main`. Netlify usa `netlify.toml` para servir la carpeta `dist/`.

Para build automático en Netlify, configura:
- **Build command:** `python generate_site.py`
- **Publish directory:** `dist`

## Datos del torneo actual

**Regional Championship - SCG CON Milwaukee - Season 4**
- 104 arquetipos con partidas
- 5,036 matches con decklist
- Top decks: Izzet Lessons, Mono-Green Landfall, Dimir Excruciator, Dimir Midrange, Simic Rhythm

## Requisitos

- Python 3.8+
- Sin dependencias externas (usa solo stdlib)

## Licencia

MIT
