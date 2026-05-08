# Detekcja sponsored / guest post / link insertion

Plan, **bez zmian w kodzie**. Decyzja o implementacji po zakończeniu D7c (three-step).

## Realia rynku polskiego (notatki z rozmowy)

1. **W PL "guest post" i "sponsored" to często to samo słowo** — kupowane zamiennie. Rozróżnianie na poziomie pipeline'u jest sztuczne. Jeden enum `paid_content` lepszy niż 2-3 osobne kategorie.

2. **Link insertion w istniejących artykułach** to dominujący kanał. Stary artykuł → broker wstawia link do nowego klienta. Cały tekst wokół linku może być stary, niepowiązany — pojedynczy "out of place" link to płatny ad.

3. **Brokerzy linków oferują "wzmianki bez linku"** (mention-only) — sygnał z linka znika, zostaje tylko nazwa brand'u w zdaniu. **To stosunkowo nowe** (ostatnie 1-2 lata na rynku PL). Detekcja wymaga rozumienia kontekstu, nie regexów.

4. **Rozwodnienie linkami** — zaawansowani publisherzy nie wstawiają wyłącznie linka do klienta, tylko mieszają go z linkami wewnętrznymi (do własnych podstron) i innymi zewnętrznymi (Wikipedia, gov, branżowe). **Goły outlink count nie wystarczy** — trzeba liczyć anomalie typu „1 dofollow do zewnętrznej domeny przy 5 nofollow do wewnętrznych".

5. **21M URL bez własnych URL** — z metadanych mamy tylko `url_finish` i hash. Domena z URL = jeden podstawowy wymiar. **Per-URL dokładność jest celem**, nie globalna statystyka domeny (bo nasze 21M to crawl szerokiego netu, a nie kilku domen).

## Co realnie chcemy odróżnić

| Klasa | Charakterystyka | Sygnały |
|---|---|---|
| `editorial` | redakcyjny, organic, brak płatności | brak komercyjnego CTA, mix linków, autor zidentyfikowany |
| `paid_content` | sponsored / guest post / advertorial | disclaimer, dominujący brand, dofollow do brand-strony, ew. rel="sponsored" |
| `link_insertion` | stary artykuł z dorzuconym linkiem | jeden link out-of-place w nieadekwatnym kontekście, anchor commercial |
| `news_aggregation` | newsroom z wieloma linkami zewnętrznymi | dużo outlinków, ale rozproszonych po wielu domenach |
| `tutorial_review` | recenzja produktu, organic | brand mentions ale balanced; często affiliate links (rel="sponsored" → ale to NIE jest sponsored content) |
| `junk` | bez treści (już wykrywane v2) | classifier `is_junk=1` |

**Pułapka:** affiliate links (rel="sponsored") to NIE to samo co sponsored content. Recenzja iPhone'a z linkiem affiliate → editorial review, nie sponsored. Trzeba odróżniać po **stosunku**: cały artykuł skupiony na 1 brandzie + CTA = paid; review wielu produktów + jedno link = editorial.

## Sygnały — pełna lista posortowana wg ROI

### Tier 1 — "darmowe", deterministyczne (regex / parser HTML)

| Sygnał | Skąd | Koszt | Potencjał |
|---|---|---|---|
| `rel="sponsored"` na linkach | raw HTML przed trafilaturą | regex | wysoki — Google standard, jawne oznaczenie |
| `rel="ugc"`, `rel="nofollow"` | raw HTML | regex | średni — UGC zwykle nie sponsored, ale guest post często ma nofollow |
| Klasy CSS (`sponsored`, `advertorial`, `partner-content`, `paid`, `promo`) | raw HTML | regex | wysoki tam gdzie wydawca taguje |
| Disclaimery PL (`artykuł sponsorowany`, `wpis (gościnny\|sponsorowany\|partnera)`, `materiał (partnera\|sponsora\|reklamowy)`, `treść sponsorowana`, `wpis płatny`) | text body | regex (case-insensitive) | wysoki — większość portali ma disclaimer |
| Disclaimery EN (`sponsored (post\|content\|article)`, `paid (content\|partnership)`, `advertorial`, `brought to you by`, `in collaboration with`, `#ad`, `#sponsored`) | text body | regex | wysoki dla EN content |
| URL containing `/sponsored/` `/partner/` `/advertorial/` `/promo/` | url_finish | string match | wysoki dla portali które separują w sitemapie |
| Author = "Materiał partnera" / "Redakcja" / brak | HTML `<meta name="author">` lub `byline` | parser | średni |
| Schema.org `creativeWork`, `isAccessibleForFree`, `sponsor` | HTML JSON-LD | parser | średni |
| `<meta property="article:section" content="(sponsored\|advertorial)">` | HTML `<head>` | parser | średni |

### Tier 2 — strukturalne, wymaga policzenia w HTML

| Sygnał | Definicja | Implementacja |
|---|---|---|
| `external_dofollow_count` | Linki bez `rel="nofollow"` ani `rel="ugc"` ani `rel="sponsored"` do innej domeny niż artykułu | parser HTML, normalizacja domeny (sld+tld) |
| `external_dofollow_concentration` | (max linków do jednej zewnętrznej domeny) / `external_dofollow_count`. Sponsorowane mają concentration → 1.0 (wszystkie linki do jednego brandu) | derivative |
| `internal_link_ratio` | Linki wewnętrzne / wszystkie linki. Niski w sponsorowanych ("rozwodnienie" zwiększa, ale CTA do brand wciąż dominuje) | derivative |
| `dofollow_to_one_domain` | (linki dofollow do najczęstszej zew. domeny). Sygnał link insertion gdy = 1 i jest "out of place" | derivative |
| `commercial_anchor_count` | Anchory zawierające `kup`, `sprawdź`, `zamów`, `oferta`, `cennik`, `kontakt`, `buy now`, `check`, `order` lub same brand names | regex na anchor text |
| `cta_phrase_count` | W tekście body: `skontaktuj się`, `zamów teraz`, `sprawdź ofertę`, `przejdź do strony`, `dowiedz się więcej u nas` | regex |
| `link_text_in_body_proximity` | Czy linki zewnętrzne są zgrupowane (jeden paragraf) czy rozproszone po artykule. Skoncentrowane = brand placement | parser z pozycjami linków w tekście |

### Tier 3 — semantyczne, wymagają LLM

| Sygnał | Definicja | Implementacja |
|---|---|---|
| `dominant_brand` | Czy artykuł ma jedną Organization/Product przewijającą się ≥X razy w tekście | enrichment Step 1 — zachować raw mention count, nie tylko deduped |
| `category_topic_mismatch` | Klasyfikator vs Step 1 kategoria, lub vs domain profile | post-processing |
| `mention_without_link` | Brand wymieniony w tekście, ale nie podlinkowany — nowa moda brokerów PL | LLM detection (regex nie wystarczy bo trzeba rozumieć "brand mention") |
| `out_of_place_paragraph` | Akapit z brandem niepowiązany kontekstowo z resztą artykułu (link insertion) | LLM ze wskaźnikiem na akapit |
| `content_type_classification` | Final klasyfikacja editorial/paid/insertion/news/review | LLM — jeden enum w meta v3 |

### Tier 4 — cross-article, wymagają agregacji

| Sygnał | Definicja | Wymaga |
|---|---|---|
| `domain_topic_anomaly` | Kategoria artykułu odbiega od profilu domeny (zwykle blog kulinarny pisze o ubezpieczeniach) | profile per domena z całego batcha |
| `link_recipient_repeat` | Domena docelowa pojawia się jako outlink z wielu różnych źródeł | global graf linków |
| `same_brand_across_unrelated_articles` | Ta sama Organization w artykułach z różnych domen i różnych kategorii | aggregate over batch |

## Rekomendowana sekwencja implementacji

### Krok 1 — Tier 1 + Tier 2 jako preprocessing (NIE LLM)

`lib/sponsored_signals.py` (nowy moduł, opt-in flag w `load_articles`). Wyciąga z surowego HTML:

```python
{
  "rel_sponsored_count": 2,
  "rel_ugc_count": 0,
  "rel_nofollow_count": 5,
  "css_sponsored_match": True,
  "url_path_sponsored": False,
  "disclaimer_pl": ["artykuł sponsorowany"],
  "disclaimer_en": [],
  "author_suspicious": True,
  "external_link_count": 8,
  "external_domains_unique": 2,
  "external_dofollow_count": 3,
  "external_dofollow_concentration": 1.0,    # all 3 dofollow → same domain
  "internal_link_ratio": 0.45,
  "commercial_anchor_count": 1,
  "cta_phrase_count": 2,
  "top_external_domain": "klient.com",
  "top_external_domain_link_count": 3
}
```

Dorzucone do `article["sponsored_signals"]`, propagowane do `final.jsonl`.

**Koszt:** zero LLM, ~20 ms / artykuł na lxml-parsing. Skalowalne na 21M.

### Krok 2 — Eyeball walidacja na 200 URL

Ręcznie oznaczyć 200 random URL z baseline5000 jako:
- `editorial` (organic),
- `paid_content` (sponsored / guest post / advertorial — łącznie, nie rozróżniamy),
- `link_insertion` (stary artykuł z wstawionym linkiem),
- `unsure`.

Jako ground truth — sprawdzić jakie sygnały Tier 1+2 mają największe AUC.

### Krok 3 — Tier 3 jako enum w meta v3

W kolejnej iteracji meta promptu dodać:

```json
"content_type": {
  "enum": ["editorial", "paid_content", "link_insertion", "review", "news", "tutorial", "uncertain"]
}
```

Plus instrukcja w prompcie z definicjami. Cost: ~1 token completion.

LLM dostaje *sygnały Tier 1+2 jako fakty w prompcie*, nie tylko surowy tekst:
> "FACTS:
>  - 3 dofollow links go to one external domain (klient.com)
>  - Article body contains: 'artykuł sponsorowany' (1 occurrence)
>  - rel="sponsored" tags found: 2"

Tym sposobem LLM ma sygnały deterministyczne + decyzję podejmuje dla edge cases. **Tier 1+2 to features, Tier 3 to klasyfikator.**

### Krok 4 — Tier 4 (cross-article) tylko jeśli potrzebne

Jeśli Tier 1+2+3 daje >85% precision/recall — Tier 4 nie potrzebny. Tier 4 to drogie agregacje na 21M, robione raz po pełnym pipeline'u.

## Specyficzne pułapki rynku PL

1. **"Wzmianki bez linku"** — pure LLM detection, nie ma sygnału strukturalnego. Zaczyna mieć sens przy Tier 3 (LLM widzi że brand wymieniony 5 razy w 800-słowowym artykule bez kontekstu redakcyjnego).

2. **Link insertion w starych artykułach** — heurystyka:
   - data publikacji artykułu vs domena klienta (jeśli mamy)
   - akapit z linkiem mający niski similarity do reszty artykułu (cosine similarity embedingu akapitu vs reszty)
   - anchor text-y typu "więcej informacji znajdziesz tutaj" — podejrzane dla tradycyjnych redakcji
   - disclaimer NIE jest obecny (insertion zwykle nie ma disclaimera, bo to nielegalne ale powszechne)

3. **Disclaimery polskie są niespójne** — różne portale używają różnych fraz. Lista do zebrania empirycznie z eyeball'a 100 sponsorowanych:
   - „Artykuł sponsorowany"
   - „Wpis sponsorowany"
   - „Wpis gościnny"
   - „Materiał partnera"
   - „Materiał sponsora"
   - „Materiał reklamowy"
   - „Treść sponsorowana"
   - „Wpis płatny"
   - „Reklama" (samodzielnie jako etykieta)
   - „Promocja" (samodzielnie jako etykieta)
   - „Advertorial"
   - „We współpracy z…"
   - „Tekst powstał we współpracy z…"
   - „Dziękujemy [marka] za…"
   - „Partner artykułu: [marka]"
   - „Patron: [marka]"

4. **Affiliate vs sponsored** — `rel="sponsored"` nie wystarczy do flag'owania. Dodatkowy heuristic: artykuł review (zawiera słowa „test", „recenzja", „porównanie", „opinia") + affiliate links → editorial review, **nie** paid content. Inaczej zalalibyśmy false-positivy.

## Outputy i metryki

W finalnym `final.jsonl` (krok 3+):

```json
{
  "url_hash": "...",
  "category": "Health, Medicine",
  "content_type": "paid_content",      // NEW
  "sponsored_score": 0.87,              // NEW: 0-1, kombinacja Tier 1+2 features
  "sponsored_signals": { ... }          // NEW: raw signals dict
}
```

Metryki do walidacji:
- precision/recall vs eyeball ground-truth (200 URL)
- distribution `content_type` per domena (sanity check)
- distribution `sponsored_score` per category (czy Health/Finance mają więcej sponsored niż Cooking?)

## Otwarte pytania (do rozstrzygnięcia po D7c)

- Czy zostawić one-pass pipeline (sygnały preprocessing → meta v3 z `content_type` jako field), czy dwupasowy (najpierw cały batch, potem cross-article Tier 4)?
- Czy `sponsored_score` to ważona kombinacja Tier 1+2 (manualne wagi) czy logistic regression na eyeballed sample?
- Jakie progi: `sponsored_score > 0.8` = paid_content, `0.4-0.8` = uncertain, `<0.4` = editorial?
- Co z artykułami afiliacyjnymi z `rel="sponsored"` ale ewidentnie editorial review? Osobny content_type `affiliate_review`?
- Czy chcemy detekcji tego samego brand'u przewijającego się w wielu artykułach (Tier 4 link_recipient_repeat)? Jest to sygnał mocny ale wymaga global aggregation.

## Status

- [x] Plan (ten plik) — zebrane sygnały, pułapki rynku PL, sekwencja implementacji
- [ ] Decyzja użytkownika: kiedy startować implementację (po D7c v3 → fair-baseline → ewentualnie po fazie 5b)
- [ ] Krok 1: `lib/sponsored_signals.py` (Tier 1+2)
- [ ] Krok 2: eyeball 200 URL — ground truth
- [ ] Krok 3: meta v3 z `content_type` enum
- [ ] Krok 4 (opcjonalnie): cross-article Tier 4

---

## Iteracja design (rev 2 — po dyskusji 2026-05-08)

### Co się zmienia

1. **Disclaimer detection przenosi się z regex (Tier 1) do LLM.**
   - Dlaczego: 21M URL w 140+ językach. Hardcoded regex PL/EN/DE/ES/FR pokryje ~70-80% ruchu, ale dla CZ, SK, RO, HU, UA, RU itd. będziemy ślepi. LLM jest natywnie wielojęzyczny — `step_meta_v3_system.md` może instruować "detect any phrase indicating paid content in the article's language".
   - Wniosek: **w confidence deterministycznym używamy WYŁĄCZNIE sygnałów strukturalnych** (HTML attributes, CSS klasy, URL path, link concentration). Disclaimery to evidence dla LLM-a, nie deterministyczna confidence.

2. **`link_insertion` jest bardziej złożone niż „1 link do 1 zewnętrznej domeny"**.
   - Ktoś może wstawić **kilka linków** w jednym akapicie (każdy do innego brand'u lub powtarzające się linki do tego samego).
   - Może być **paru reklamodawców w jednym artykule** (broker zbierający 3 zlecenia w jednym wpisie) — wtedy `external_dofollow_concentration` jest **niska** ale wciąż to insertion.
   - Lepsza metryka: `out_of_context_link_count` (linki do akapitów semantycznie odbiegających od reszty artykułu) — ale to wymaga LLM lub embedding-similarity per paragraf.
   - Tier 2 deterministyczny może mierzyć tylko **akumulację linków zewnętrznych w jednym akapicie** (clustering pozycyjny) — łapie część przypadków, nie wszystkie.

3. **`sponsored_subtype` enum zaakceptowany.** Wartości:
   - `null` — gdy `sponsored=false`
   - `full_sponsored` — cały artykuł jest reklamą jednej marki + disclaimer (klasyczny advertorial)
   - `link_insertion` — artykuł na różny temat z wstawionymi linkami komercyjnymi (1 lub więcej, może być kilku reklamodawców)
   - `affiliate_review` — recenzja produktu/produktów z affiliate linkami; redakcyjna treść, ale `rel="sponsored"` na linkach (Google standard dla affiliate)
   - `advertorial` — sponsored content udający artykuł (zatarcie redakcyjnej linii)

4. **NOWE: `sponsored_justification` (kilka słów)** — wyjaśnienie decyzji.
   - Cel: audit trail, debugging edge cases, kalibracja (Chain-of-thought-lite — LLM zmuszony do justyfikacji często myśli się dokładniej).
   - Format: zwięźle, ≤ 120 znaków. Konkretny sygnał, nie ogólnik.
   - Dobre przykłady: `"rel=sponsored found, brand X mentioned 5x"`, `"single dofollow to klient.com in unrelated paragraph"`, `"disclaimer 'Materiał partnera' + dominant brand"`, `"affiliate links + balanced multi-product review"`.
   - Złe przykłady: `"looks sponsored"`, `"some commercial signals"`, `"unsure"`.
   - Jeśli `sponsored=false` → `justification` może być pusty (`""`) lub `"no signals"`.

### Finalna schema dla `meta_v3` (propozycja)

```json
{
  "language": "...",
  "category": "...",
  "title": "...",
  "meta_description": "...",
  "h1": "...",
  "article_summary": "...",
  "sponsored": true,
  "sponsored_subtype": "link_insertion",
  "sponsored_justification": "single dofollow to brand-X.com in cooking article context"
}
```

Plus deterministyczna kalkulacja w post-processing (NIE w schemacie LLM):

```json
{
  "sponsored_signals": { "rel_sponsored_count": 0, "css_match": false, "url_path": false,
                         "external_dofollow_count": 1, "concentration": 1.0,
                         "top_external_domain": "brand-x.com", ... },
  "sponsored_confidence": "medium"   // wyliczone z sygnałów + LLM verdict
}
```

### Reguły wyznaczania `sponsored_confidence`

| Stan | Confidence |
|---|---|
| `sponsored=true` ORAZ (rel_sponsored_count > 0 OR css_match OR url_path) | **high** (twardy sygnał wydawcy) |
| `sponsored=true` ORAZ structural signals (concentration ≥ 0.7 lub top_external_domain_link_count ≥ 3) | **medium** |
| `sponsored=true` ALE brak structural signals | **low** (LLM judgment, np. tylko disclaimer w treści lub dominujący brand) |
| `sponsored=false` | confidence pominięta lub `null` |

**Kluczowa obserwacja:** `confidence=high` nie oznacza „LLM bardzo pewny", tylko „mamy twardy sygnał strukturalny od wydawcy". `confidence=low` oznacza że tylko LLM widzi sponsored, deterministyka milczy. To uczciwa interpretacja, nie LLM-self-reported pewność.

### Co z subtype dla link_insertion z wieloma reklamodawcami?

Otwarte pytanie: czy potrzebujemy granularnej informacji „ile reklamodawców"? Na tym etapie — nie. `subtype=link_insertion` wystarczy, a deterministyczne `top_external_domain_link_count` daje wskazówkę:
- = 1 → pojedynczy insertion
- 2-3 → broker łączący zlecenia
- ≥ 4 → klasyczny full_sponsored z wieloma linkami do brandu

Można dodać pole `n_advertisers` w post-processing (count distinct external domains z dofollow), ale to nice-to-have.

---

## Iteracja design (rev 3 — po dyskusji 2026-05-08, druga seria)

### Trzy korekty zmieniające architekturę

**1. `rel="sponsored"` jest słabym sygnałem w praktyce.**

Reklamodawca **walczy o dofollow** — bo to cały sens kupowania linka SEO: zyskać PageRank do swojej domeny.

**Tabela przepływu PageRank wg rel attribute:**

| rel | Przepływ PageRank | Komentarz |
|---|---|---|
| brak (dofollow) | **pełny** | klasyczny link SEO — pożądany przez kupującego |
| `rel="nofollow"` | **częściowy / mniejszy** | od 2019 Google traktuje jako wskazówkę, nie dyrektywę. Może (ale nie musi) przekazać PageRank. W praktyce — mniej niż dofollow, ale nie zero |
| `rel="ugc"` | **zero** | user-generated content, Google nie przekazuje PageRank |
| `rel="sponsored"` | **zero** | reklamowy / płatny, Google nie przekazuje PageRank |

Wydawca uczciwie oznaczający `rel="sponsored"` sprzedaje **dramatycznie słabszy produkt SEO** (zerowy PageRank vs pełny dofollow). Większość brokerów i klientów tego unika. W praktyce:

- `rel="sponsored"` mają głównie portale duże, zorganizowane (np. Onet, WP) z osobnymi sekcjami advertorialowymi (compliance, FTC, IAB) — i tak nieliczne.
- Mniejsze blogi i side-hustle publishery dają **dofollow** nawet gdy płatne — żeby klient zapłacił więcej i wracał.
- `rel="ugc"` to inny przypadek (komentarze, fora) — rzadko sponsored content jako artykuł.
- `rel="nofollow"` jest częstsze niż `sponsored` — dawniej (przed 2019) standard dla wszystkich „niepewnych" linków, czasem stosowany przez wydawców jako ostrożny kompromis (mniej PageRank niż dofollow, ale więcej niż sponsored). Klient żądający SEO też tego unika.

**Wniosek:** `rel="sponsored"` daje `confidence=high` *gdy jest*, ale **prawie zawsze go nie ma**. Nie można na tym budować detektora. Trzeba szukać innych dźwigni.

Pomocniczy sygnał: **wzorzec `nofollow` z external link to "ostrożny" wydawca** — może być sygnałem advertorial w pewnych kontekstach (zwłaszcza gdy zwykłe redakcyjne linki w tym samym portalu są dofollow). Ale to słaby sygnał, nie diagnostyczny.

**2. Out-of-context paragraph similarity nie zadziała na zaawansowanych reklamodawcach.**

Mądrzy brokerzy **piszą lub kupują tekst napisany pod link** — akapit z linkiem semantycznie pasuje do reszty artykułu. Jeśli klient sprzedaje matce, broker zamawia tekst „Najlepsze materace dla niemowląt" na blogu rodzicielskim — embedding similarity będzie wysoka, bo wszystko jest o niemowlętach.

**Out-of-context** łapie tylko amatorów. Już teraz rynek PL wykonuje zaawansowane SEO content marketing — większość sponsored to **konteksowo pasujące insertion**, nie wstawione na siłę.

**Wniosek:** `out_of_context_link_count` zostawiamy jako pomocniczy sygnał (łapie amatorów ~20-30% rynku), ale nie traktujemy jako głównego. Sygnały z **kontekstu komercyjnego całego artykułu** są ważniejsze:
- czy artykuł realnie odpowiada na pytanie czytelnika, czy jest „lookbook" produktu
- czy autor ma głos (osobisty język, opinia, anegdoty), czy bezosobowy press-release ton
- czy są CTA komercyjne, choćby zawoalowane

To są sygnały **stylistyczne** — domena LLM, nie deterministyki.

**3. Subtype `brand_mentions` — nowa moda rynku PL.**

Brokerzy oferują **wzmianki bez linków** (mention-only). Klient płaci za pojawienie się nazwy brandu w treści, **bez aktywnego linka**. Sygnał z linka znika, zostaje tylko nazwa marki w zdaniu. Powody:
- niektóre branże (medycyna, finanse) mają prawne ograniczenia na linkowanie reklamowe → mention-only obchodzi to
- brand-awareness jako cel sam w sobie (nie SEO)
- niektóre algorytmy detekcji link-spamu są oszukane gdy brak linka

Detekcja: **wymaga LLM**. Regex nie pomoże, bo brand mention to organic-looking zdanie typu „warto rozważyć produkty marki X". Heurystyki:
- nazwa brandu pojawia się ≥ N razy
- otoczenie jest pochwalne ("polecam", "warto", "jakość", "renomowana firma")
- brak innych marek z tej samej kategorii (nie ma porównania, tylko jeden brand)
- artykuł ma redakcyjną formę, ale jeden brand dominuje

### Zaktualizowane sygnały (rev 3)

**Twarde sygnały strukturalne (Tier 1+2):**

| Sygnał | Realny weight | Komentarz |
|---|---|---|
| `rel="sponsored"` | low (gdy jest = high precision, ale rzadkie) | Mało kto go używa — kupujący żąda dofollow |
| Disclaimer w treści | LLM detection (wielojęzyczność) | Rynek PL czasem oznacza, czasem nie |
| URL path `/sponsored/` | medium | Tylko duże portale z kategoryzowaną strukturą |
| CSS klasy | medium | Często usuwane przez trafilatura, trzeba parsować raw HTML |
| `external_dofollow_count` ≥ 3 do jednej domeny | medium | Klasyczny full_sponsored |
| `external_dofollow_count` = 1, ale anchor commercial | medium | Klasyczny link_insertion |
| Brak innych zewnętrznych linków poza brand-stronami | high | Bardzo specyficzny — strona po nic innego |

**Sygnały LLM (Tier 3) — najważniejsze w rev 3:**

| Sygnał | Implementacja |
|---|---|
| `dominant_brand_in_organic_context` | LLM widzi jedną Organization wymienianą wielokrotnie w pozytywnym świetle, brak konkurencji |
| `editorial_voice_absent` | LLM ocenia ton: "press-release" vs "personalny artykuł" |
| `commercial_cta_subtle` | LLM łapie CTA ukryte ("warto rozważyć", "godne uwagi") nie tylko jawne ("kup teraz") |
| `disclaimer_any_language` | LLM rozumie disclaimery w PL/CZ/SK/HU/UA/RU/etc. |
| `mention_only_pattern` | LLM widzi ≥ N wzmianek o brand bez linków, w pozytywnym kontekście |
| `unrelated_brand_in_context_text` | LLM widzi że tematycznie powiązany akapit zawiera link/wzmiankę o brand niezwiązany z core topic ("przy okazji", "również", subtelne zmiany tematu) |

### Subtypes (rev 3)

```
sponsored_subtype:
  null               ← when sponsored=false
  full_sponsored     ← cały artykuł reklamą jednej marki, klasyczny advertorial
  link_insertion     ← wstawione linki komercyjne (1+ reklamodawców), tekst może być semantycznie pasujący
  brand_mentions     ← wzmianki bez aktywnych linków (mention-only ads, nowa moda PL)
  affiliate_review   ← redakcyjna recenzja z affiliate links (rel="sponsored" lub linkowanie partnerskie)
  advertorial        ← sponsored content udający redakcyjny artykuł, granica zatarta
```

`affiliate_review` zostaje osobno — trzeba go odróżnić, bo to **editorial intent** mimo `rel="sponsored"` lub linków partnerskich. Filtr SEO `sponsored=true` w wielu zastosowaniach **NIE chce** flagować recenzji konsumenckich.

### Reguły confidence (rev 3)

W świetle tego, że `rel="sponsored"` jest rzadkie, confidence trzeba przebudować:

| Stan | Confidence |
|---|---|
| `sponsored=true` AND (rel="sponsored" OR url_path_sponsored OR css_match) | **high** (twardy sygnał wydawcy — gdy jest, jest pewny) |
| `sponsored=true` AND LLM disclaimer detection AND structural support (≥3 linki dofollow do jednej domeny lub dominant brand ≥5 wzmianek) | **high** |
| `sponsored=true` AND (LLM disclaimer detection OR strong structural — concentration ≥0.7 OR commercial anchor + dofollow) | **medium** |
| `sponsored=true` AND tylko LLM judgment (style, ton, mention pattern) bez twardych sygnałów | **low** |
| `sponsored=false` | `null` |

**Kluczowa zmiana:** disclaimer wykrywany przez LLM **liczy się jak twardy sygnał** (jeśli LLM precision jest dobra), bo to wprost wypowiedziane oznaczenie wydawcy. Walidujemy to eyeballiem — jeśli LLM łapie disclaimery z >95% precision, traktujemy je jako tier 1 strukturalny.

### Realna ekonomia detekcji w PL

Bazując na rynku, realnie szukamy:
- ~5-15% URL z baseline5000 to **paid_content** w jakiejś formie (full_sponsored + link_insertion + brand_mentions razem).
- Z tego ~30-50% ma jakiś detekowalny structural signal.
- Reszta wymaga LLM-style detection.
- `affiliate_review` jako edge case — trzeba aktywnie odróżniać żeby nie generować false-positive (review iPhone'a z affiliate linkami **NIE** jest paid content).

---

## Iteracja design (rev 4 — uproszczenie do binary editorial vs advertising)

### Główne uproszczenie

Zamiast multi-class enum `content_type`, redukujemy do **jednej decyzji binarnej**: `sponsored = true/false`. Editorial wynika automatycznie z negacji — nie ma osobnego detektora dla „redakcyjny".

```
sponsored = false  →  artykuł redakcyjny / własny / internal
sponsored = true   →  artykuł reklamowy (sponsored, guest post, link insertion, brand mentions)
```

`sponsored_subtype` zostaje opcjonalny — drilldown dla tych, co chcą wiedzieć **jak** to jest sponsored, nie tylko **czy**.

### Dlaczego to lepsze

1. **Mniej decyzji do podjęcia przez LLM** = mniej szansy na błąd. Jedna binarna klasyfikacja zamiast 5-klasowego enum.
2. **Łatwiejsza walidacja** — eyeball ground truth to po prostu „sponsored: 0/1" per URL, nie wybór z 5 etykiet.
3. **Editorial nie wymaga aktywnego rozpoznawania** — to jest default. To zmiana paradygmatu: zamiast „klasyfikuj typ", pytamy „czy to wygląda na sponsored — a jeśli nie, zostaje redakcyjne".

### Nowy główny KPI: per-domain ratio

Po pełnym runie agregujemy z `final.jsonl` per `domain`:

```
domena                    | n_articles | sponsored% | confidence
─────────────────────────────────────────────────────────────
biznews.com.pl            |       847  |    62%    | high
pomocedlaseniora.pl       |       234  |    81%    | high   ← link farm
duzyportal.pl             |     12000  |     4%    | high   ← real editorial
maly-blog-rodzicielski.pl |        45  |    23%    | medium
```

To jest **realny biznesowy output** — od razu mówi które domeny to content farms / paid networks vs prawdziwe redakcje. Dla SEO link auditingu, dla Google quality signals, dla content marketing analysis — bezpośrednia wartość.

**Konsekwencja samplingowa:** żeby uzyskać `sponsored_ratio` dla domeny nie potrzebujemy wszystkich 21M URL. Wystarczy **reprezentatywny sample per domena** (np. 50-100 URL random per domain). Ratio jest proxy dla całego site'u. To może drastycznie zmniejszyć computation cost do faktycznego mapowania rynku.

### Schema (finalna, rev 4)

```json
{
  // ... pozostałe pola meta_v3 ...
  "sponsored": true,
  "sponsored_subtype": "link_insertion",
  "sponsored_justification": "single dofollow to brand-x.com in cooking article context"
}
```

`sponsored_subtype`:
- `null` lub pominięte gdy `sponsored=false`
- Wartości gdy `sponsored=true`: `full_sponsored` / `link_insertion` / `brand_mentions` / `affiliate_review` / `advertorial`

Post-processing dorzuca:
```json
{
  "sponsored_signals": { ... raw deterministic signals ... },
  "sponsored_confidence": "high|medium|low"  // z reguł na deterministycznych sygnałach
}
```

### Dwa wymiary outputu

Z tej schemy automatycznie dostajemy **dwa wymiary klasyfikacji**:

1. **Per artykuł**: `sponsored: bool` z `confidence` i `subtype` (jeśli sponsored).
2. **Per domena**: `sponsored_ratio` z liczebnością artykułów i statystykami pewności.

Oba są niezależne i komplementarne. Raport końcowy może pokazywać oba — top sponsored articles + top sponsored domains.

### Plan walidacji (rev 2)

1. **Pre-implement**: ustalić listę 30-50 disclaimer-fraz z eyeballa baseline5000 — wstrzyknąć w prompt LLM jako positive examples (few-shot). To działa cross-language tylko częściowo, ale daje LLM "wzór do rozpoznawania".
2. **Eyeball 200 URL** ręcznie z baseline5000:
   - oznacz `sponsored=0/1`
   - oznacz `subtype` (jeśli =1)
   - notuj który deterministyczny sygnał był obecny
3. **Mierz**:
   - precision/recall LLM-only `sponsored=0/1`
   - precision/recall structural-only (rel/css/path)
   - precision/recall combined
   - confusion matrix per `subtype`
   - czy `confidence=high` faktycznie ma >95% precision (jeśli nie — kalibracja progów)
4. **Iteruj prompt** na podstawie błędów eyeballa.

---

## Iteracja design (rev 5 — owner-commercial discovery, smoke v4 implementacja)

### Co odkryliśmy podczas smoke v4

Pierwszy smoke n=5 dał 5/5 sponsored=True. Wszystkie justifications wyglądały OPISOWO legitymnie, ale 5/5 to za dużo na realnym rynku.

Patrząc na konkretne przypadki:
- `pomocedlaseniora.pl` — model klasyfikował artykuł publishera jako `link_insertion` z powodu wielu linków komercyjnych. **Ale to były linki do SKLEPU TEGO SAMEGO publishera** — owner-commercial, nie sponsored.

**Diagnoza:** model nie ma w prompcie informacji o domenie publishera. Patrząc na surowy markdown nie odróżnia internal od external linków.

### Kluczowa korekta: trójpodział kategorii

Konceptualnie mamy 3 typy treści (binary `sponsored=true/false` koduje 2 i 3):

1. **Editorial** — neutralna treść, brak komercyjnej intencji → `sponsored=false`
2. **Owner-commercial** — publisher promuje SWÓJ sklep / usługi / brand na SWOJEJ domenie → `sponsored=false` (z odpowiednim justification "owner-commercial: ...")
3. **Third-party sponsored** — płatne content od kogoś z poza publisher's organization → `sponsored=true`

`sponsored=true` zarezerwowane wyłącznie dla **third-party** paid placement. Owner-commercial to legitymny e-commerce content marketing, nie manipulacja PageRank, nie warto flag'ować.

### Implementacja domain context (2026-05-08)

Każde wywołanie `process_sponsored_v1` przekazuje teraz domenę publishera do user-prompta:

```python
domain = (article.get("domain") or "").lower().strip()
user = f"""Classify the article below.

PUBLISHER DOMAIN: {domain}
Links to {domain} (and its subdomains) are INTERNAL — publisher's own pages.
INTERNAL links/mentions = NOT third-party sponsored (publisher's own commercial content).
Only links/mentions of OTHER domains count as third-party sponsored signals.

<article>
{article['text']}
</article>"""
```

System prompt zaktualizowany — sekcja "PUBLISHER DOMAIN — critical context" z regułą:

> "Sponsored signals (link concentration, dominant brand, CTA pushing purchases, disclaimers) must point at content/links/brands EXTERNAL to the publisher's domain. Internal commerce = not sponsored."

Dodane przykłady w prompt'cie:
- Example 7 — owner-commercial (pomocedlaseniora.pl style) → `sponsored=false`
- Example 8 — single-product news (Apple iPhone news) → `sponsored=false`
- Example 9 — press release adopted (`[Informacje prasowe]`) → `sponsored=true`
- Example 10 — single-product review (krytyczna recenzja) → `sponsored=false`
- Example 4 zmieniony: affiliate review → `sponsored=false` (editorial)

### `affiliate_review` przeniesiony z subtype do editorial

User decision: "affiliate_review to editorial wlasny". Schema zmieniona — usunięto `affiliate_review` z enum `sponsored_subtype`. Affiliate-style review (np. multi-product comparison z Allegro/Amazon affiliate links) = sponsored=false z justification "affiliate review — balanced multi-product comparison with critical opinions".

Konsekwencja: `subtype` enum zredukowany do `[null, full_sponsored, link_insertion, brand_mentions, advertorial]`.

### Smoke v4 po fixie (n=5)

| URL | Domain | sponsored | Komentarz |
|---|---|---|---|
| Stone veneer | graniteks.pl | **False** ✓ | "owner-commercial: product page on publisher's own domain" |
| Würth Power tape | biznews.com.pl | True | "press release for Würth, no critical voice" |
| pomocedlaseniora shop | pomocedlaseniora.pl | **False** ✓ | "owner-commercial: internal links only" |
| PLAY network hotspot | biznews.com.pl | True | "promotes PLAY (play.pl/playnow.pl) external + W ofercie sieci PLAY" — user confirmed |
| Szkoła rysunku | biznews.com.pl | True | "explicit '[Informacje prasowe]' tag + szkolarysunku.waw.pl external" |

**3/3 z biznews.com.pl** zaklasyfikowane jako sponsored — biznews.com.pl jest portalem typu publish-for-pay, więc to spójne z eyeballem.
**2/2 owner-commercial** poprawnie sponsored=false.
**1/5 borderline** (Würth) — granica press-release-vs-news, ale w polskim rynku PR placement to faktycznie sponsored.

### Pliki implementacyjne (rev 5)

```
prompts/step_sponsored_v1_system.md   ← rev 4 prompt + domain context + 10 examples
prompts/schema_sponsored_v1.json      ← subtype enum bez affiliate_review
lib/pipeline_fourstep_v1.py           ← user-prompt z PUBLISHER DOMAIN
scripts/run_fourstep_v1.py            ← orchestrator 4 priority queues
```

### Status implementacji

- [x] Rev 1-4 design + decyzje
- [x] Implementacja v4 fourstep + smoke n=5
- [x] Owner-commercial discovery + fix
- [x] Domain context w user-promptcie
- [ ] Pełen run na 500 URL z baseline5000 — czeka na zielone światło
- [ ] Eyeball n=200 ground truth
- [ ] Walidacja precision/recall
- [ ] Per-domain `sponsored_ratio` agregacja jako KPI

## Referencje

- Google rel attributes for outbound links: <https://developers.google.com/search/docs/crawling-indexing/qualify-outbound-links>
- FTC disclosure guidelines (US): <https://www.ftc.gov/business-guidance/resources/disclosures-101-social-media-influencers>
- IAB Polska — kodeks dobrych praktyk (sponsored content, content marketing)
