# Black Friday · Juegos de mesa (Top 1000 BGG)

Panel que, dos veces al día (10:00 y 22:00, hora de Madrid), rastrea las tiendas
especializadas españolas, se queda solo con los juegos del **top 1000 de BoardGameGeek y
sus expansiones**, y muestra el **mejor precio** de cada uno con su **descuento sobre PVP**.

Todo corre en GitHub Actions (sin Claude, sin servidor). El panel es una página estática en GitHub Pages:
https://matebru.github.io/bgg-blackfriday/

## Cómo funciona

| Paso | Qué hace |
|---|---|
| `scraper/bgg.py` | Top 1000 del ranking BGG + nombres alternativos (los títulos en castellano salen de ahí) + expansiones. Se cachea en `data/bgg.json` y se rehace cada 7 días. |
| `scraper/stores.py` | Un scraper por tienda: PrestaShop genérico (Dracotienda, Mathom, JMR, Dungeon Marvels, Jugamos Otra), Magento (Zacatrus), WooCommerce (Ludoteca Maldita), ASP.NET (Kaburi, Generación X) y ASP clásico (MasQueOca). |
| `scraper/match.py` | Casa cada producto con BGG: nombre exacto → «Juego: subtítulo» (= expansión) → difuso ≥94 %. Descarta accesorios y segunda mano. Admite ediciones en castellano e inglés (marcadas EN); el resto de idiomas fuera. |
| `scraper/run.py` | Agrupa por juego, elige la tienda más barata con stock, calcula PVP y descuento, guarda `docs/data.json`. |

**PVP** = el precio tachado más alto entre las tiendas que venden ese juego (ignorando tachados >1,6× la mediana, que suelen ser errores o precios inflados).
**Descuento** = 1 − mejor precio / PVP.

Si una tienda falla, el panel la marca en rojo; si tiene datos de menos de 30 h, los reutiliza y la marca en naranja.

## Mantenimiento

- Probar una sola tienda: *Actions → Black Friday scrape → Run workflow* → campo *stores* → `Zacatrus` (o varias separadas por coma).
- Forzar recarga del ranking BGG: *Run workflow* → marcar *refresh_bgg*.
- Añadir/quitar tiendas: diccionario `STORES` al final de `scraper/stores.py`.
- Pasado Black Friday: **Actions → Black Friday scrape → ⋯ → Disable workflow**.
- Tests (sin red): `pip install -r requirements.txt pytest && pytest`.
