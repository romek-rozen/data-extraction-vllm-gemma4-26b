# scraper/

Skrypt do pobierania całych domen w formacie zgodnym z `websites/<md5>/{html.gz, json.gz}` (format Mateusza).

## Instalacja

Lokalny venv (nie globalny system):

```bash
python3 -m venv scraper/.venv
scraper/.venv/bin/pip install -r scraper/requirements.txt
scraper/.venv/bin/crawl4ai-setup    # pobranie chromium dla playwright (~500 MB, jednorazowo)
```

## Format wyjściowy

```
out_dir/
└── <md5(url)>/
    ├── html.gz      # surowy HTML, gzip
    └── json.gz      # {url, url_finish, http_code, http_code_finish, headers[], title, description}
```

`headers` — lista `{level: int, text: str}` dla h1-h6 z dokumentu (kolejność DOM).

## Użycie

```bash
# Z sitemap.xml (najszybsze, idzie po liście znanych URL):
scraper/.venv/bin/python scraper/scrape_domain.py \
    --sitemap https://example.com/sitemap.xml \
    --out-dir websites_new/ \
    --concurrency 4

# Z BFS od homepage (gdy brak sitemapy):
scraper/.venv/bin/python scraper/scrape_domain.py \
    --domain https://example.com \
    --max-urls 500 \
    --out-dir websites_new/ \
    --concurrency 4

# Z pliku z URL-ami (jedna linia = jeden URL):
scraper/.venv/bin/python scraper/scrape_domain.py \
    --urls-file my_urls.txt \
    --out-dir websites_new/ \
    --concurrency 4
```

## Anty-bot

`crawl4ai` używa playwright/chromium z domyślnym anti-detection (`magic=True`) — radzi sobie z Cloudflare, Akamai itp. dla większości stron. Dla bardzo agresywnych zabezpieczeń może być potrzebne `--simulate-user` (już on by default) lub headers override (TODO).

## Idempotencja

Jeśli `out_dir/<md5(url)>/` już istnieje, URL jest skipowany. Aby wymusić ponowne pobranie — usuń katalog.

## Filtry

- Extensions binarne (`.pdf`, `.jpg`, `.zip`, `.css`, `.js`, `.json`, `.xml` ...) skipowane na poziomie discovery (sitemap, BFS, file).
- BFS sprawdza `Content-Type: text/html` przed pobraniem (oszczędza czas na nie-HTML).
- `crawl_urls` (Playwright) sprawdza `Content-Type` z response headers PO fetchu — jeśli nie zawiera `text/html`, URL jest pominięty (filtr na PDF/JSON/binary serwowane bez extension w URL).
- BFS trzyma się jednej domeny (no off-domain crawl).
- Wszystkie URL przechodzą przez `should_skip_url` (scheme http/https + extension blacklist).

## robots.txt

**Domyślnie respektowany.** Filtruje URL-e na 3 etapach:

1. Discovery (sitemap, BFS, file) — odfiltrowuje przed dodaniem do listy.
2. BFS — sprawdza Disallow PRZED każdym fetchem (oszczędza requesty).
3. crawl_urls — drugi safety net przed właściwym pobraniem.

Per-domena cache (jeden fetch `robots.txt` na domenę). Crawl-delay z `robots.txt` jest ładowany (ale obecnie informacyjnie — TODO: wymusić w rate limit).

Obsługa edge cases:
- robots.txt 404/410 → wszystko dozwolone (RFC 9309 default)
- robots.txt 5xx lub błąd → fail-open (dozwolone) z warningiem w logu
- pobieranie przez `requests` z explicit User-Agentem (omija problem stdlib `RobotFileParser.read()` który dostaje 403 od Cloudflare)

### Opt-out

Tylko gdy świadomie chcesz zignorować robots.txt (research, własna domena):

```bash
scraper/.venv/bin/python scraper/scrape_domain.py \
    --domain https://example.com \
    --max-urls 500 \
    --out-dir websites_new/ \
    --ignore-robots
```

Skrypt wyrzuci WARNING w logu.

### Custom User-Agent

```bash
--user-agent "Mojabotka/1.0 (+https://moja-strona.pl/bot-info)"
```

Używany zarówno do pobrania `robots.txt`, do BFS GET-ów, jak i przekazany do crawl4ai (Playwright). Jeśli chcesz mieć nazwany bot z whitelisty robots.txt — podaj jego dokładną nazwę.
