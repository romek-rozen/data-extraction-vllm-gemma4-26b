"""Thread-safe JSONL reporter z idempotencją po url_hash.

Append-only — rerun tego samego URL nie nadpisuje, ale dodaje wpis. Loadery
na poziomie Step 2 / dashboard powinny dedup'ować po url_hash.

Dla strict idempotencji: użyj `load_existing_hashes()` przed enqueue → skip.
"""

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class JsonlReporter:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(line)

    def load_existing_hashes(self, only_ok: bool = True) -> set[str]:
        """Zwróć set url_hash z dotychczasowego JSONL (idempotencja przy restarcie).

        Domyślnie (only_ok=True) bierze tylko rekordy z ok=True — pozwala resume ponowić failsy.
        Z only_ok=False zachowuje stare zachowanie (wszystkie rekordy).
        """
        if not self.path.exists():
            return set()
        hashes: set[str] = set()
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if only_ok and not rec.get("ok", False):
                        continue
                    h = rec.get("url_hash")
                    if h:
                        hashes.add(h)
                except json.JSONDecodeError:
                    continue
        return hashes

    def load_records(self) -> list[dict]:
        """Wczytaj wszystkie wpisy. Dedup po url_hash (zostaje OSTATNI dla danego klucza)."""
        if not self.path.exists():
            return []
        by_hash: dict[str, dict] = {}
        order: list[dict] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                h = rec.get("url_hash")
                if h:
                    by_hash[h] = rec
                else:
                    order.append(rec)
        return list(by_hash.values()) + order
