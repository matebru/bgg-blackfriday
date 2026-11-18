"""Cliente HTTP compartido: sesión con reintentos, cabeceras de navegador y pausa entre peticiones."""
import time
import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class Http:
    """Una instancia por tienda: respeta su propio ritmo (delay) y guarda cookies."""

    def __init__(self, delay: float = 1.0, timeout: int = 30):
        self.delay = delay
        self.timeout = timeout
        self._last = 0.0
        self._lock = threading.Lock()
        self.s = requests.Session()
        retry = Retry(
            total=4,
            backoff_factor=2,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
        )
        self.s.mount("https://", HTTPAdapter(max_retries=retry))
        self.s.mount("http://", HTTPAdapter(max_retries=retry))
        self.s.headers.update(
            {
                "User-Agent": UA,
                "Accept-Language": "es-ES,es;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            }
        )

    def _wait(self):
        with self._lock:
            dt = time.time() - self._last
            if dt < self.delay:
                time.sleep(self.delay - dt)
            self._last = time.time()

    def get(self, url, **kw):
        self._wait()
        r = self.s.get(url, timeout=self.timeout, **kw)
        r.raise_for_status()
        return r

    def post(self, url, **kw):
        self._wait()
        r = self.s.post(url, timeout=self.timeout, **kw)
        r.raise_for_status()
        return r
