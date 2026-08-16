"""Non-blocking logging.Handler that batches records and ships them to a
Loki push API (e.g. Grafana Cloud). Only wired in when LOKI_URL is set —
local dev and any host without it configured behave exactly as before."""
import base64
import logging
import threading
import time
from collections import deque

import httpx


class LokiHandler(logging.Handler):
    def __init__(self, url, username, password, labels, flush_interval=3.0, max_batch=200):
        super().__init__()
        self.url = url.rstrip("/") + "/loki/api/v1/push"
        self.labels = labels
        self.flush_interval = flush_interval
        self.max_batch = max_batch
        self._buffer = deque()
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=5.0)
        if username:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            self._client.headers["Authorization"] = f"Basic {token}"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def emit(self, record):
        # Never let a logging call itself raise or block the request path.
        try:
            line = self.format(record)
            ts_ns = str(int(record.created * 1_000_000_000))
            with self._lock:
                self._buffer.append((ts_ns, line))
        except Exception:
            pass

    def _run(self):
        while not self._stop.is_set():
            time.sleep(self.flush_interval)
            self._flush()

    def _flush(self):
        with self._lock:
            if not self._buffer:
                return
            batch = [self._buffer.popleft() for _ in range(min(len(self._buffer), self.max_batch))]
        payload = {"streams": [{"stream": self.labels, "values": [[ts, line] for ts, line in batch]}]}
        try:
            resp = self._client.post(self.url, json=payload)
            if resp.status_code >= 300:
                print(f"[loki_handler] push failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            print(f"[loki_handler] push error: {e}")

    def close(self):
        self._stop.set()
        self._flush()
        self._client.close()
        super().close()
