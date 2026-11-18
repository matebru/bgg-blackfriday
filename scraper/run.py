"""Ejecución completa: BGG -> tiendas -> casado -> mejor precio -> docs/data.json

Uso:
    python -m scraper.run                  # todas las tiendas
    python -m scraper.run Zacatrus Mathom  # solo algunas (para probar)
"""
import json
import os
import re
import statistics
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import bgg
from .match import Matcher, norm
from .stores import STORES
from . import http as http_mod

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data.json"
CACHE = ROOT / "data" / "cache"
HISTORY = ROOT / "data" / "history.json"

INCLUDE_FOREIGN = False      # alemán, francés, italiano... fuera (inglés y castellano sí entran)
STALE_MAX_HOURS = 30         # si una tienda falla, reutilizamos su último scrape si es reciente
PVP_OUTLIER = 1.6            # un tachado > 1.6x la mediana se ignora (precio inflado o error)


def now():
    return datetime.now(timezone.utc)


def scrape_store(name, fn):
    t0 = time.time()
    cache = CACHE / f"{re.sub(r'[^a-z0-9]+', '_', name.lower())}.json"
    try:
        offers = fn()
        if not offers:
            raise RuntimeError("0 productos (¿ha cambiado la web?)")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"at": now().isoformat(), "offers": offers}, ensure_ascii=False))
        status = {"ok": True, "products": len(offers), "seconds": round(time.time() - t0)}
        print(f"✔ {name}: {len(offers)} productos en {status['seconds']}s", flush=True)
        return offers, status
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        status = {"ok": False, "error": str(e)[:200], "seconds": round(time.time() - t0),
                  "last_response": getattr(http_mod.LAST, "info", None)}
        if cache.exists():
            c = json.loads(cache.read_text())
            age = now() - datetime.fromisoformat(c["at"])
            if age < timedelta(hours=STALE_MAX_HOURS):
                status.update({"stale_from": c["at"], "products": len(c["offers"])})
                print(f"✘ {name}: {e} -> reutilizo datos de {c['at']}", flush=True)
                return c["offers"], status
        print(f"✘ {name}: {e}", flush=True)
        return [], status


def pvp_of(offers):
    regs = [o["regular"] for o in offers]
    med = statistics.median(regs)
    sane = [r for r in regs if r <= med * PVP_OUTLIER] or regs
    return max(sane)


def main(only=None):
    data_bgg = bgg.load()
    matcher = Matcher(data_bgg)
    stores = {k: v for k, v in STORES.items() if not only or k in only}

    with ThreadPoolExecutor(max_workers=len(stores)) as ex:
        results = dict(zip(stores, ex.map(lambda kv: scrape_store(*kv), stores.items())))

    groups, matched, by_how = {}, 0, {}
    for store, (offers, _) in results.items():
        for o in offers:
            m = matcher.match(o["name"])
            if not m or (m["foreign"] and not INCLUDE_FOREIGN):
                continue
            matched += 1
            by_how[m["how"]] = by_how.get(m["how"], 0) + 1
            if m["kind"] == "base":
                key = f"b{m['bgg_id']}"
            elif m["bgg_id"]:
                key = f"e{m['bgg_id']}"
            else:
                key = f"x{m['base_id']}:{norm(o['name'])}"
            g = groups.setdefault(key, {"m": m, "offers": []})
            g["offers"].append({**o, "lang": m["lang"]})

    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    stamp = now().isoformat()
    items = []
    for key, g in groups.items():
        m, offers = g["m"], g["offers"]
        # una oferta por tienda e idioma (la más barata con stock si la tienda lo lista dos veces)
        per_store = {}
        for o in sorted(offers, key=lambda o: (o["stock"] == "out", o["price"])):
            per_store.setdefault((o["store"], o["lang"]), o)
        offers = sorted(per_store.values(), key=lambda o: o["price"])
        available = [o for o in offers if o["stock"] != "out"]
        best = (available or offers)[0]
        pvp = pvp_of(offers)
        discount = round(100 * (1 - best["price"] / pvp), 1) if pvp else 0.0

        h = history.get(key)
        if not h or best["price"] < h["min"]:
            history[key] = h = {"min": best["price"], "store": best["store"], "at": stamp}

        items.append({
            "key": key,
            "name": best["name"],                 # nombre de la tienda (en castellano)
            "bgg_name": m["bgg_name"] or "",      # nombre oficial BGG si lo conocemos
            "kind": m["kind"],
            "bgg_id": m["bgg_id"] or m["base_id"],
            "base_name": m["base_name"],
            "rank": m["rank"],
            "img": best["img"] or data_bgg_image(data_bgg, m["base_id"]),
            "price": best["price"],
            "pvp": pvp,
            "discount": max(discount, 0.0),
            "store": best["store"],
            "url": best["url"],
            "stock": best["stock"],
            "lang": best["lang"],
            "min_seen": h["min"],
            "offers": [{"store": o["store"], "price": o["price"], "url": o["url"], "stock": o["stock"], "lang": o["lang"]}
                       for o in offers],
        })

    items.sort(key=lambda i: -i["discount"])
    out = {
        "generated_at": stamp,
        "bgg_generated_at": data_bgg["generated_at"],
        "stores": {k: v[1] for k, v in results.items()},
        "stats": {"offers_matched": matched, "items": len(items), "by_how": by_how},
        "items": items,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, separators=(",", ":")))
    print(f"\n{len(items)} juegos/expansiones del top en oferta -> {OUT}")
    print("Casados por método:", by_how)
    failed = [k for k, v in out["stores"].items() if not v["ok"]]
    if failed:
        print("Tiendas con error:", ", ".join(failed))


_img_cache = {}


def data_bgg_image(data_bgg, base_id):
    if not _img_cache:
        _img_cache.update({g["id"]: g.get("image", "") for g in data_bgg["games"]})
    return _img_cache.get(base_id, "")


if __name__ == "__main__":
    env = [x.strip() for x in os.environ.get("STORES", "").split(",") if x.strip()]
    main(sys.argv[1:] or env or None)
