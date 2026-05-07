"""Lazy-loaded tokenizer Gemma 4 — szybki, in-process, bez HTTP.

Używa `tokenizers` (Rust) bezpośrednio z `tokenizer.json` w katalogu modelu.
Pomija buggy `tokenizer_config.json` w `bg-digitalservices` quancie.

Speed: ~2 ms per markdown article (vs ~50 ms przez vLLM /tokenize).
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TOKENIZER_PATH = Path.home() / "models/gemma4-26b-nvfp4-bg/tokenizer.json"
_tokenizer = None


def get_tokenizer():
    """Singleton — załaduj raz, używaj wszędzie."""
    global _tokenizer
    if _tokenizer is None:
        from tokenizers import Tokenizer
        _tokenizer = Tokenizer.from_file(str(_TOKENIZER_PATH))
        logger.info(f"Tokenizer załadowany: {_TOKENIZER_PATH}")
    return _tokenizer


def count_tokens(text: str) -> int:
    return len(get_tokenizer().encode(text).ids)


def truncate_to_tokens(text: str, max_tokens: int) -> tuple[str, int]:
    """Przytnij tekst do max_tokens. Zwraca (przycięty_tekst, faktyczna_liczba_tokenów).

    Jeśli tekst mieści się w budżecie — zwraca oryginał.
    """
    tok = get_tokenizer()
    encoded = tok.encode(text)
    if len(encoded.ids) <= max_tokens:
        return text, len(encoded.ids)
    truncated_ids = encoded.ids[:max_tokens]
    truncated_text = tok.decode(truncated_ids)
    return truncated_text, len(truncated_ids)
