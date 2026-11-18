"""Casa los productos de las tiendas con el top de BGG (juegos base + expansiones).

Orden de intentos para cada producto:
  1. Nombre exacto (normalizado) contra nombres BGG: principal + alternativos (castellano incluido)
     del juego base y nombres de sus expansiones.
  2. "Base: subtítulo" o "Base - subtítulo" cuyo prefijo es un juego del top
     -> se considera expansión de ese juego.
  3. Coincidencia difusa alta (>= FUZZY_MIN) contra el índice.
Todo lo que no case se descarta (no está en el top).
"""
import re
import unicodedata

from rapidfuzz import fuzz, process

FUZZY_MIN = 94

# Productos que no son juegos (accesorios, merchandising) o no son comparables
NOT_A_GAME = re.compile(
    r"\b(fundas?|sleeves?|insert[oa]?s?|organizador|organizer|playmat|tapete|neopreno|camiseta|taza|"
    r"puzzle|p[oó]ster|artbook|libro de arte|mochila|bolsa|caja de almacenaje|storage|"
    r"monedas de metal|fichas de metal|metal coins|set de dados|pack de dados|dice set|"
    r"repuesto|recambio|segunda mano|usado|tara|golpeado|da[nñ]ad[oa]|defectuos[oa]|"
    r"pintad[oa]s?|pinturas?|pincel|figura coleccionable|funko|peluche|llavero|pin)\b",
    re.I,
)
# Inglés y castellano valen; el resto de idiomas se descarta.
ENGLISH = re.compile(
    r"[\(\[]\s*(ingl[eé]s|english|eng|en)\s*[\)\]]|\b(english edition|edici[oó]n inglesa|"
    r"versi[oó]n inglesa|en ingl[eé]s|in english)\b",
    re.I,
)
FOREIGN = re.compile(
    r"[\(\[]\s*(alem[aá]n|german|deutsch|de|franc[eé]s|french|fr|italiano|italian|it|"
    r"portugu[eé]s|pt|polaco|polish|holand[eé]s|dutch|nl)\s*[\)\]]|\b(deutsche ausgabe|"
    r"edici[oó]n (alemana|francesa|italiana)|en (alem[aá]n|franc[eé]s|italiano))\b",
    re.I,
)
NOISE = [
    r"[\(\[][^\)\]]*[\)\]]",                      # todo lo que vaya entre paréntesis/corchetes
    r"\s[-–]\s*juego (de|cooperativo|competitivo|familiar)\b.*$",   # coletillas de Kaburi
    r"\bjuego de (mesa|tablero|cartas)\b",
    r"\b(edici[oó]n|version|versi[oó]n)\s+(en\s+)?(castellano|espa[nñ]ol|espanola)\b",
    r"\b(castellano|espa[nñ]ol|spanish|catal[aà]|catala)\b",
    r"\b(nueva|new|segunda|2a|2ª|tercera|3a|revisada|revised|deluxe|de lujo|kickstarter|ks|retail|"
    r"edici[oó]n|edition|ed\.?|ed)\b",
    r"\+\s*promos?\b",
    r"\bpromos?\b",
]
NOISE_RE = [re.compile(p, re.I) for p in NOISE]


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s):
    s = s or ""
    for rx in NOISE_RE:
        s = rx.sub(" ", s)
    s = strip_accents(s.lower())
    s = s.replace("&", " y ").replace("’", "'")
    s = re.sub(r"[¡!¿?\"'`´.,;:/\\|_*#~^]+", " ", s)
    s = re.sub(r"\s*-\s*", " ", s)
    s = re.sub(r"\b(el|la|los|las|the|le|les|der|die|das)\b", " ", s)  # artículos fuera
    return " ".join(s.split())


def split_prefixes(name):
    """'Dune: Imperium - Immortality' -> ['Dune: Imperium', 'Dune'] (del más largo al más corto)."""
    cuts = [m.start() for m in re.finditer(r"\s*(?::|\s[-–]\s)\s*(?=\S)", name)]
    return [name[:c].strip() for c in reversed(cuts) if name[:c].strip()]


class Matcher:
    def __init__(self, bgg):
        self.games = {g["id"]: g for g in bgg["games"]}
        self.index = {}      # norm_name -> (kind, bgg_id, base_id)
        self.base_index = {}
        # Expansiones primero, luego bases: ante choque de nombres gana el base.
        for g in sorted(bgg["games"], key=lambda g: -g["rank"]):
            for e in g.get("expansions", []):
                n = norm(e["name"])
                if len(n) >= 3:
                    self.index[n] = ("expansion", e["id"], g["id"], e["name"])
        for g in sorted(bgg["games"], key=lambda g: -g["rank"]):
            for name in set(g.get("names") or []) | {g["name"]}:
                n = norm(name)
                if len(n) >= 2:
                    self.index[n] = ("base", g["id"], g["id"], g["name"])
                    self.base_index[n] = g["id"]
        self._keys = list(self.index)

    def match(self, product_name):
        """Devuelve dict con kind/bgg_id/base_id/bgg_name/how, o None."""
        if not product_name or NOT_A_GAME.search(product_name):
            return None
        foreign = bool(FOREIGN.search(product_name))
        lang = "en" if ENGLISH.search(product_name) else "es"
        n = norm(product_name)
        if not n:
            return None

        hit = self.index.get(n)
        if hit:
            return self._res(hit, "exacto", foreign, lang)

        for pref in split_prefixes(product_name):
            pn = norm(pref)
            if len(pn) >= 4 and pn in self.base_index:
                base_id = self.base_index[pn]
                # ¿El subtítulo es una expansión conocida con otro nombre? Probamos difuso solo entre ellas.
                exps = self.games[base_id].get("expansions") or []
                if exps:
                    choices = {norm(e["name"]): e for e in exps}
                    best = process.extractOne(n, list(choices), scorer=fuzz.token_set_ratio, score_cutoff=92)
                    if best:
                        e = choices[best[0]]
                        return self._res(("expansion", e["id"], base_id, e["name"]), "prefijo+difuso", foreign, lang)
                return self._res(("expansion", None, base_id, None), "prefijo", foreign, lang)

        if len(n) >= 6:
            best = process.extractOne(n, self._keys, scorer=fuzz.ratio, score_cutoff=FUZZY_MIN)
            if best:
                return self._res(self.index[best[0]], "difuso", foreign, lang)
        return None

    def _res(self, hit, how, foreign, lang="es"):
        kind, bgg_id, base_id, bgg_name = hit
        base = self.games[base_id]
        return {
            "kind": kind,
            "bgg_id": bgg_id,
            "base_id": base_id,
            "base_name": base["name"],
            "bgg_name": bgg_name,
            "rank": base["rank"],
            "how": how,
            "foreign": foreign,
            "lang": lang,
        }
