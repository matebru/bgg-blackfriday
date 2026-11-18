"""Scrapers de cada tienda. Todos devuelven una lista de ofertas:

    {"store", "name", "url", "price", "regular", "img", "stock"}

price   = precio al que se vende hoy
regular = precio tachado que muestra la tienda (o el mismo precio si no hay tachado)
stock   = "in" | "pre" (preventa/reserva) | "out"
"""
import re
from urllib.parse import urljoin, unquote, urlparse

from bs4 import BeautifulSoup

from .http import Http

MAX_PAGES = 1200


# ---------------------------------------------------------------- utilidades

def eur(text):
    """'1.234,56 €' -> 1234.56 ; '26.99' -> 26.99 ; None si no hay número."""
    if text is None:
        return None
    t = str(text).replace("\xa0", " ").strip()
    m = re.search(r"(\d{1,3}(?:\.\d{3})+|\d+),(\d{1,2})", t)
    if m:
        return float(m.group(1).replace(".", "") + "." + m.group(2))
    m = re.search(r"\d+(?:\.\d+)?", t)
    return float(m.group(0)) if m else None


def stock_from_text(text):
    t = (text or "").lower()
    if re.search(r"preventa|pre-venta|reserva|pr[oó]ximamente|pre-?order", t):
        return "pre"
    if re.search(r"agotado|sin stock|no disponible|fuera de stock|out of stock|out_of_stock|sin existencias", t):
        return "out"
    return "in"


def slug_name(url):
    """Nombre a partir del slug de la URL (para títulos truncados con '...')."""
    path = unquote(urlparse(url).path).rstrip("/").split("/")[-1]
    path = re.sub(r"\.html?$", "", path)
    path = re.sub(r"^\d+-", "", path)          # id PrestaShop
    path = re.sub(r"-\d{8,14}$", "", path)     # EAN al final
    return path.replace("-", " ").strip()


def soft(call, have, url=""):
    """Ejecuta una petición; si falla a mitad de catálogo, nos quedamos con lo leído hasta ahí."""
    try:
        return call()
    except Exception as e:  # noqa: BLE001
        if have:
            print(f"  ! {url}: {e} -> paro aquí y me quedo con {have} productos", flush=True)
            return None
        raise


def offer(store, name, url, price, regular, img, stock):
    if not name or not url or not price or price <= 0:
        return None
    if not regular or regular < price:
        regular = price
    return {
        "store": store,
        "name": " ".join(name.split()),
        "url": url,
        "price": round(price, 2),
        "regular": round(regular, 2),
        "img": img or "",
        "stock": stock,
    }


# ---------------------------------------------------------------- PrestaShop

def _presta_page(store, base, html):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for it in soup.select(".product-miniature, article[data-id-product]"):
        a = it.select_one(".product-title a, .product-name a, h2 a, h3 a") or it.select_one("a[href]")
        if not a:
            continue
        url = urljoin(base, a.get("href", "").split("?")[0])
        title_el = it.select_one(".product-title, .product-name, h2, h3")
        name = (a.get("title") or (title_el.get_text(" ", strip=True) if title_el else "")).strip()
        if not name or name.endswith("...") or name.endswith("…"):
            name = slug_name(url) or name
        price_el = it.select_one(".price, [itemprop=price], .product-price")
        reg_el = it.select_one(".regular-price")
        img_el = it.select_one("img")
        img = ""
        if img_el:
            img = (img_el.get("data-full-size-image-url") or img_el.get("data-src")
                   or img_el.get("data-original") or img_el.get("src") or "")
            img = urljoin(base, img)
        flags = " ".join(
            [it.get_text(" ", strip=True)[-300:]]
            + [" ".join(e.get("class", [])) for e in it.select("[class*=stock], [class*=availab], .product-flag")]
        )
        o = offer(store, name, url,
                  eur(price_el.get("content") or price_el.get_text()) if price_el else None,
                  eur(reg_el.get_text()) if reg_el else None,
                  img, stock_from_text(flags))
        if o:
            out.append(o)
    return out


def prestashop(store, base, categories, delay=1.0):
    http = Http(delay=delay)
    offers, seen = [], set()
    for cat in categories:
        for page in range(1, MAX_PAGES):
            url = urljoin(base, cat) + f"?page={page}"
            r = soft(lambda: http.get(url), len(offers), url)
            if r is None:
                break
            items = _presta_page(store, base, r.text)
            new = [o for o in items if o["url"] not in seen]
            if not new:   # PrestaShop repite la última página cuando te pasas
                break
            for o in new:
                seen.add(o["url"])
            offers.extend(new)
    return offers


# ---------------------------------------------------------------- Zacatrus (Magento)

def zacatrus():
    store, base = "Zacatrus", "https://zacatrus.es/"
    http = Http(delay=3.0)  # su robots.txt pide Crawl-delay: 3
    offers, seen = [], set()
    for page in range(1, MAX_PAGES):
        r = soft(lambda: http.get(f"{base}juegos-de-mesa.html?p={page}"), len(offers))
        if r is None:
            break
        soup = BeautifulSoup(r.text, "lxml")
        items = soup.select("li.product-item, .product-item")
        new = 0
        for it in items:
            a = it.select_one("a.product-item-link")
            if not a:
                continue
            url = a.get("href", "").split("?")[0]
            if url in seen:
                continue
            seen.add(url)
            new += 1
            final = it.select_one('[data-price-type="finalPrice"]')
            old = it.select_one('[data-price-type="oldPrice"]')
            img = it.select_one("img.product-image-photo") or it.select_one("img")
            out_of_stock = it.select_one(".stock.unavailable, .unavailable") is not None
            o = offer(store, a.get_text(" ", strip=True), url,
                      float(final["data-price-amount"]) if final else None,
                      float(old["data-price-amount"]) if old else None,
                      (img.get("src") or "").split("?")[0] if img else "",
                      "out" if out_of_stock else stock_from_text(it.get_text(" ", strip=True)[-200:]))
            if o:
                offers.append(o)
        if not new:
            break
    return offers


# ---------------------------------------------------------------- Ludoteca Maldita (WooCommerce)

def ludoteca_maldita():
    store, base = "Ludoteca Maldita", "https://ludotecamaldita.com"
    http = Http(delay=1.0)
    offers = []
    for page in range(1, 200):
        r = soft(lambda: http.get(f"{base}/wp-json/wc/store/v1/products?per_page=100&page={page}"), len(offers))
        if r is None:
            break
        items = r.json()
        if not items:
            break
        for p in items:
            pr = p.get("prices") or {}
            div = 10 ** int(pr.get("currency_minor_unit", 2))
            price = int(pr.get("price") or 0) / div
            regular = int(pr.get("regular_price") or 0) / div
            imgs = p.get("images") or []
            name = BeautifulSoup(p.get("name", ""), "lxml").get_text()
            o = offer(store, name, p.get("permalink"), price, regular,
                      imgs[0].get("src") if imgs else "",
                      "in" if p.get("is_in_stock") else ("pre" if p.get("is_on_backorder") else "out"))
            if o:
                offers.append(o)
        if page >= int(r.headers.get("x-wp-totalpages") or page):
            break
    return offers


# ---------------------------------------------------------------- Kaburi (ASP.NET, paginación por POST)

def _kaburi_parse(html):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for idm in soup.select("meta[name=Id]"):
        card = idm.parent
        meta = {m.get("name"): m.get("content", "") for m in card.select("meta[name]")}
        price = eur(meta.get("sale-price")) or eur(meta.get("main-price"))
        o = offer("Kaburi", meta.get("title"), meta.get("link"), price, eur(meta.get("main-price")),
                  meta.get("image_link"),
                  {"in_stock": "in", "out_of_stock": "out", "preorder": "pre"}.get(meta.get("availability"), "in"))
        if o:
            out.append(o)
    return soup, out


def kaburi():
    base = "https://www.kaburi.es/search-result.aspx"
    http = Http(delay=1.5)
    offers, seen = [], set()
    for badge in ("MENU|TABLERO",):
        url = f"{base}?Badge={badge}"
        soup, items = _kaburi_parse(http.get(url).text)
        page = 1
        while True:
            new = [o for o in items if o["url"] not in seen]
            if not new:
                break
            for o in new:
                seen.add(o["url"])
            offers.extend(new)
            page += 1
            if page > MAX_PAGES:
                break
            form = soup.select_one("form#form1")
            if not form:
                break
            data = {}
            for inp in form.select("input[name]"):
                if inp.get("type") in ("submit", "button", "image", "checkbox", "radio") and not inp.has_attr("checked"):
                    continue
                data[inp["name"]] = inp.get("value", "")
            data["Paginar_p"] = str(page)
            data["Paginar_pc"] = "n"
            action = urljoin(url, form.get("action") or url)
            r = soft(lambda: http.post(action, data=data), len(offers))
            if r is None:
                break
            soup, items = _kaburi_parse(r.text)
    return offers


# ---------------------------------------------------------------- Generación X (ASP.NET, ?pagina=N)

def generacion_x():
    store, base = "Generación X", "https://www.generacionx.es/"
    http = Http(delay=1.0)
    offers, seen = [], set()
    for cat in ("juegos-de-mesa", "juegos-de-cartas"):
        for page in range(1, MAX_PAGES):
            r = soft(lambda: http.get(f"{base}{cat}?pagina={page}"), len(offers))
            if r is None:
                break
            soup = BeautifulSoup(r.text, "lxml")
            new = 0
            for t in soup.select(".filetitle"):
                a = t.select_one("a[href]")
                if not a:
                    continue
                url = urljoin(base, a["href"])
                if url in seen:
                    continue
                seen.add(url)
                new += 1
                span = t.select_one("span[title]")
                name = span["title"] if span else a.get_text(" ", strip=True)
                card = t
                for _ in range(6):
                    if card.parent is None:
                        break
                    card = card.parent
                    if card.select_one("img") and card.select_one(".fileprice"):
                        break
                reg = card.select_one(".importe3PVPIniSinDTO")
                cur = card.select_one(".importe3PVPIni")
                img = card.select_one("img")
                av = card.select_one(".fileavailability")
                o = offer(store, name, url, eur(cur.get_text()) if cur else None,
                          eur(reg.get_text()) if reg else None,
                          urljoin(base, img.get("data-src") or img.get("src") or "") if img else "",
                          stock_from_text(av.get_text(" ", strip=True) if av else ""))
                if o:
                    offers.append(o)
            if not new:
                break
    return offers


# ---------------------------------------------------------------- MasQueOca (ASP clásico)

def masqueoca():
    store, base = "MasQueOca", "https://www.masqueoca.com/tienda/"
    http = Http(delay=1.0)
    offers, seen = [], set()
    for tipo in (1, 2):  # 1 = juegos de mesa, 2 = juegos de cartas
        for page in range(1, MAX_PAGES):
            r = soft(lambda: http.get(f"{base}buscardo.asp?xs=0&t=1&pact={page}&idtipojuego={tipo}"), len(offers))
            if r is None:
                break
            html = r.content.decode("windows-1252", errors="replace")
            soup = BeautifulSoup(html, "lxml")
            new = 0
            for el in soup.find_all(string=re.compile(r"Precio")):
                block = el.parent
                for _ in range(8):
                    links = [a for a in block.select('a[href*="producto.asp"]') if a.get_text(strip=True)]
                    if links or block.parent is None:
                        break
                    block = block.parent
                links = [a for a in block.select('a[href*="producto.asp"]') if a.get_text(strip=True)]
                if not links:
                    continue
                url = urljoin(base, links[0]["href"])
                if url in seen:
                    continue
                prices = [eur(p) for p in re.findall(r"\d+,\d{2}\s*€", block.get_text(" "))]
                prices = [p for p in prices if p]
                if not prices:
                    continue
                seen.add(url)
                new += 1
                img, anc = None, block
                for _ in range(4):
                    img = next((i for i in anc.select('img[src*="imagesjuegos"]')
                                if i.find_parent("a") and i.find_parent("a").get("href") == links[0]["href"]), None)
                    if img or anc.parent is None:
                        break
                    anc = anc.parent
                o = offer(store, links[0].get_text(" ", strip=True), url, min(prices), max(prices),
                          urljoin(base, img["src"]) if img else "",
                          stock_from_text(block.get_text(" ")))
                if o:
                    offers.append(o)
            if not new:
                break
    return offers


# ---------------------------------------------------------------- registro de tiendas

STORES = {
    "Zacatrus": zacatrus,
    "Dracotienda": lambda: prestashop("Dracotienda", "https://dracotienda.com/",
                                      ["1715-juegos-de-tablero", "1729-juegos-de-cartas"]),
    "Mathom": lambda: prestashop("Mathom", "https://mathom.es/", ["es/2-juegos-de-mesa"]),
    "Juegos de la Mesa Redonda": lambda: prestashop(
        "Juegos de la Mesa Redonda", "https://juegosdelamesaredonda.com/",
        ["7-juegos-de-tablero", "6-juegos-de-cartas", "9-juegos-para-ninos", "64-juegos-de-importacion",
         "76-juegos-de-dados", "215-wargames", "108-expansiones-de-juegos"]),
    "Dungeon Marvels": lambda: prestashop("Dungeon Marvels", "https://dungeonmarvels.com/",
                                          ["10-juegos-de-tablero", "183-juegos-de-cartas-no-coleccionables"]),
    "Jugamos Otra": lambda: prestashop("Jugamos Otra", "https://jugamosotra.com/", ["es/24-juegos"]),
    "Ludoteca Maldita": ludoteca_maldita,
    "Kaburi": kaburi,
    "Generación X": generacion_x,
    "MasQueOca": masqueoca,
}
