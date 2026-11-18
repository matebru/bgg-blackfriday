"""Cliente HTTP compartido: huella TLS de Chrome (curl_cffi), reintentos, pausa entre peticiones
y registro de la última respuesta para diagnosticar bloqueos."""
import threading
import time

try:
    from curl_cffi import requests as creq  # imita a Chrome: pasa filtros anti-bot básicos
except ImportError:  # pragma: no cover
    creq = None
import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
RETRY_STATUS = {429, 500, 502, 503, 504}

# Última respuesta vista en este hilo (cada tienda corre en su propio hilo)
LAST = threading.local()


class Http:
    """Una instancia por tienda: respeta su propio ritmo (delay) y guarda cookies."""

    def __init__(self, delay: float = 1.0, timeout: int = 30, retries: int = 4):
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self._last = 0.0
        self._lock = threading.Lock()
        if creq:
            self.s = creq.Session(impersonate="chrome")
        else:
            self.s = requests.Session()
            self.s.headers.update({"User-Agent": UA})
        self.s.headers.update({"Accept-Language": "es-ES,es;q=0.9,en;q=0.8"})

    def _wait(self):
        with self._lock:
            dt = time.time() - self._last
            if dt < self.delay:
                time.sleep(self.delay - dt)
            self._last = time.time()

    def _req(self, method, url, **kw):
        err = None
        for attempt in range(self.retries + 1):
            self._wait()
            try:
                r = self.s.request(method, url, timeout=self.timeout, **kw)
                LAST.info = {"url": url, "status": r.status_code, "head": (r.text or "")[:300]}
                if r.status_code in RETRY_STATUS and attempt < self.retries:
                    time.sleep(min(60, 3 * 2 ** attempt))
                    continue
                r.raise_for_status()
                return r
            except Exception as e:  # noqa: BLE001
                err = e
                if attempt < self.retries and not _is_http_4xx(e):
                    time.sleep(min(60, 3 * 2 ** attempt))
                    continue
                raise
        raise err

    def get(self, url, **kw):
        return self._req("GET", url, **kw)

    def post(self, url, **kw):
        return self._req("POST", url, **kw)


def _is_http_4xx(e):
    resp = getattr(e, "response", None)
    code = getattr(resp, "status_code", None)
    return code is not None and 400 <= code < 500 and code != 429
