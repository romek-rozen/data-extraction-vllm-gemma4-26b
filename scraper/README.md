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

- Domyślnie wyłączane są: extensions binarne (`.pdf`, `.jpg`, `.zip`, ...), URL z fragmentami `#`, query strings parametryczne (TODO: opcja)
- BFS trzyma się jednej domeny (no off-domain crawl)
