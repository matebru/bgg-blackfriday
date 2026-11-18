"""Construye data/bgg.json: top N de BoardGameGeek + nombres alternativos + expansiones.

Fuentes (públicas, sin login):
  - https://boardgamegeek.com/browse/boardgame/page/{1..10}   (100 juegos por página)
  - https://boardgamegeek.com/api/geekitems?objectid=ID...      (nombres alternativos, imagen)
  - https://boardgamegeek.com/api/geekitem/linkeditems?...      (expansiones)

El ranking apenas se mueve en una semana, así que se cachea y solo se rehace
si el fichero tiene más de BGG_MAX_AGE_DAYS días.
"""
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from .http import Http

BASE = "https://boardgamegeek.com"
TOP_N = 1000
BGG_MAX_AGE_DAYS = 7
OUT = Path(__file__).resolve().parent.parent / "data" / "bgg.json"
# Novedades 2025-2026 fuera del top (lista generada aparte desde la búsqueda avanzada de BGG)
NEW_FILE = OUT.parent / "bgg_new.json"

# Solo nos interesan nombres en alfabeto latino (castellano, inglés, catalán...).
def is_latin(name):
    return all(ord(c) < 0x250 or 0x2010 <= ord(c) <= 0x201F for c in name)


def _ranking(http: Http, top_n: int):
    games = []
    for page in range(1, (top_n + 99) // 100 + 1):
        html = http.get(f"{BASE}/browse/boardgame/page/{page}").text
        soup = BeautifulSoup(html, "lxml")
        for row in soup.select("tr#row_"):
            a = row.select_one("a.primary")
            rank_td = row.select_one(".collection_rank")
            if not a or not rank_td:
                continue
            m = re.search(r"/boardgame/(\d+)", a.get("href", ""))
            rank_txt = rank_td.get_text(strip=True)
            if not m or not rank_txt.isdigit():
                continue
            games.append({"id": int(m.group(1)), "name": a.get_text(strip=True), "rank": int(rank_txt)})
        print(f"  BGG ranking página {page}: {len(games)} juegos", flush=True)
    games.sort(key=lambda g: g["rank"])
    return games[:top_n]


def _details(http: Http, game: dict):
    url = f"{BASE}/api/geekitems?objectid={game['id']}&objecttype=thing&subtype=boardgame&nosession=1"
    item = http.get(url).json().get("item", {}) or {}
    alts = {a.get("name", "").strip() for a in item.get("alternatenames") or []}
    alts.add(item.get("name") or game["name"])
    game["names"] = sorted(n for n in alts if n and is_latin(n))
    game["year"] = item.get("yearpublished")
    game["image"] = item.get("imageurl") or ""
    return game


def _expansions(http: Http, game: dict):
    exps, page = [], 1
    while True:
        url = (
            f"{BASE}/api/geekitem/linkeditems?ajax=1&linkdata_index=boardgameexpansion&nosession=1"
            f"&objectid={game['id']}&objecttype=thing&pageid={page}&showcount=50&sort=name&subtype=boardgameexpansion"
        )
        j = http.get(url).json()
        items = j.get("items") or []
        for it in items:
            exps.append({"id": int(it["objectid"]), "name": it.get("name", "")})
        total = int((j.get("config") or {}).get("numitems") or 0)
        if not items or len(exps) >= total or page > 20:
            break
        page += 1
    game["expansions"] = exps
    return game


def _one(http, game):
    for attempt in range(3):
        try:
            _details(http, game)
            _expansions(http, game)
            return game
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                print(f"  ! BGG {game['id']} {game['name']}: {e}", flush=True)
                game.setdefault("names", [game["name"]])
                game.setdefault("expansions", [])
                return game
            time.sleep(5)


def build(top_n: int = TOP_N):
    http = Http(delay=0.25)
    games = _ranking(http, top_n)
    if len(games) < top_n * 0.9:
        raise RuntimeError(f"BGG devolvió solo {len(games)} juegos del ranking")
    done = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for _ in ex.map(lambda g: _one(http, g), games):
            done += 1
            if done % 100 == 0:
                print(f"  BGG detalles: {done}/{len(games)}", flush=True)
    data = {"generated_at": datetime.now(timezone.utc).isoformat(), "games": games}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    n_exp = sum(len(g["expansions"]) for g in games)
    print(f"BGG: {len(games)} juegos, {n_exp} expansiones -> {OUT}", flush=True)
    return data


def load(force: bool = False):
    """Top cacheado (se reconstruye si caduca) + novedades 2025-2026 si existe data/bgg_new.json."""
    data = _load_top(force)
    if NEW_FILE.exists():
        top_ids = {g["id"] for g in data["games"]}
        extra = []
        for g in json.loads(NEW_FILE.read_text()).get("games", []):
            if g["id"] in top_ids:
                continue
            g["rank"] = g.get("bgg_rank")       # ranking real de BGG (puede no tener)
            g["is_new"] = True
            extra.append(g)
        data = {**data, "games": data["games"] + extra}
        print(f"BGG: {len(top_ids)} del top + {len(extra)} novedades 2025-2026", flush=True)
    return data


def _load_top(force: bool = False):
    if OUT.exists() and not force:
        data = json.loads(OUT.read_text())
        age = datetime.now(timezone.utc) - datetime.fromisoformat(data["generated_at"])
        if age.days < BGG_MAX_AGE_DAYS:
            return data
        try:
            return build()
        except Exception as e:  # noqa: BLE001
            print(f"! No se pudo refrescar BGG ({e}); uso la copia de {data['generated_at']}", flush=True)
            return data
    return build()

if __name__ == "__main__":
    build(int(sys.argv[1]) if len(sys.argv) > 1 else TOP_N)
