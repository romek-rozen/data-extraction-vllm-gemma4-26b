# SPO Distribution Analysis

**Timestamp:** 2026-05-09 16:08:44
**Source:** `final_results/2026-05-09_00-21-48__spo_v1_mns32_full/entities_spo.jsonl`
**Records:** 10,425 ok / total triples: 97,132

## 1. Coverage

| Metric | Value |
|---|---|
| ok records | 10,425 |
| records with triples | 10,424 (100.0%) |
| total triples | 97,132 |
| mean triples/record | 9.32 |

## 2. Per-record distribution

| Metric | min | p50 | p95 | max | mean |
|---|---|---|---|---|---|
| n_entities | 1.00 | 11.00 | 22.00 | 49.00 | 12.41 |
| n_central | 0.00 | 2.00 | 5.00 | 6.00 | 2.28 |
| n_triples | 0.00 | 9.00 | 14.00 | 23.00 | 9.32 |
| s_unmatched | 0.00 | 0.00 | 3.00 | 19.00 | 0.50 |
| o_unmatched | 0.00 | 0.00 | 1.00 | 13.00 | 0.21 |

### n_triples histogram
```
    0.000 –   1.150 |  1
    1.150 –   2.300 |  3
    2.300 –   3.450 |  12
    3.450 –   4.600 | █ 68
    4.600 –   5.750 | █████ 364
    5.750 –   6.900 | ███████████ 745
    6.900 –   8.050 | ██████████████████████████████████████████████████ 3252
    8.050 –   9.200 | ███████████████████████ 1530
    9.200 –  10.350 | ███████████████████████████ 1800
   10.350 –  11.500 | ███████████ 760
   11.500 –  12.650 | ██████████ 672
   12.650 –  13.800 | ██████ 414
   13.800 –  14.950 | ████ 304
   14.950 –  16.100 | █████ 361
   16.100 –  17.250 | █ 69
   17.250 –  18.400 |  28
   18.400 –  19.550 |  22
   19.550 –  20.700 |  14
   20.700 –  21.850 |  3
   21.850 –  23.000 |  3
```

## 3. Confidence distribution

| confidence (all triples) | 0.00 | 0.95 | 0.99 | 1.00 | 0.94 |

### Histogram
```
    0.000 –   0.050 |  1
    0.050 –   0.100 |  0
    0.100 –   0.150 |  0
    0.150 –   0.200 |  0
    0.200 –   0.250 |  0
    0.250 –   0.300 |  0
    0.300 –   0.350 |  0
    0.350 –   0.400 |  0
    0.400 –   0.450 |  0
    0.450 –   0.500 |  0
    0.500 –   0.550 |  3
    0.550 –   0.600 |  0
    0.600 –   0.650 |  0
    0.650 –   0.700 |  9
    0.700 –   0.750 |  0
    0.750 –   0.800 |  11
    0.800 –   0.850 | ██ 3026
    0.850 –   0.900 |  1024
    0.900 –   0.950 | ██████████████████████████████████████████████████ 65027
    0.950 –   1.000 | █████████████████████ 28031
```

### Confidence buckets

| Range | Count | % |
|---|---|---|
| <0.5 | 1 | 0.0% |
| 0.5-0.7 | 3 | 0.0% |
| 0.7-0.85 | 109 | 0.1% |
| 0.85-0.95 | 32,576 | 33.5% |
| ≥0.95 | 64,443 | 66.3% |

## 4. Confidence by triple position (KEY: cap=8 decision)

Pytanie: czy trójki na pozycji 9-14 mają niższe confidence niż 1-8?
Jeśli tak → cap=8 odetnie szum bez utraty wartościowych trójek.

| pos | n | mean conf | median conf | min | max |
|---|---|---|---|---|---|
| 1 | 10,424 | 0.966 | 0.980 | 0.850 | 1.000 |
| 2 | 10,424 | 0.953 | 0.950 | 0.850 | 1.000 |
| 3 | 10,421 | 0.947 | 0.950 | 0.850 | 1.000 |
| 4 | 10,409 | 0.944 | 0.950 | 0.800 | 1.000 |
| 5 | 10,341 | 0.941 | 0.950 | 0.000 | 1.000 |
| 6 | 9,977 | 0.938 | 0.950 | 0.700 | 1.000 |
| 7 | 9,232 | 0.935 | 0.950 | 0.500 | 1.000 |
| 8 | 7,884 | 0.934 | 0.950 | 0.500 | 1.000 |
| 9 | 5,980 | 0.933 | 0.950 | 0.500 | 1.000 |
| 10 | 4,450 | 0.931 | 0.950 | 0.700 | 1.000 |
| 11 | 2,650 | 0.933 | 0.950 | 0.800 | 1.000 |
| 12 | 1,890 | 0.932 | 0.950 | 0.800 | 1.000 |
| 13 | 1,218 | 0.931 | 0.950 | 0.800 | 1.000 |
| 14 | 804 | 0.929 | 0.950 | 0.800 | 1.000 |
| 15 | 500 | 0.929 | 0.950 | 0.800 | 1.000 |
| 16 | 247 | 0.929 | 0.950 | 0.800 | 0.990 |
| 17 | 139 | 0.934 | 0.950 | 0.850 | 0.990 |
| 18 | 70 | 0.937 | 0.950 | 0.850 | 0.990 |
| 19 | 42 | 0.939 | 0.950 | 0.850 | 0.990 |
| 20 | 20 | 0.939 | 0.950 | 0.900 | 0.990 |


**Drop-off:** mean conf positions 1-8 = **0.945**, positions 9+ = **0.933** (Δ +0.012)

## 5. Most common relation types

Top 30 (z ogólnej liczby 2546 unikalnych):

| relation_type | count | % | mean conf |
|---|---|---|---|
| `has_property` | 23,537 | 24.2% | 0.950 |
| `has_part` | 8,672 | 8.9% | 0.944 |
| `provides` | 6,358 | 6.5% | 0.932 |
| `located_in` | 5,950 | 6.1% | 0.944 |
| `requires` | 5,529 | 5.7% | 0.944 |
| `uses` | 4,370 | 4.5% | 0.940 |
| `is_a` | 3,934 | 4.1% | 0.949 |
| `related_to` | 3,861 | 4.0% | 0.919 |
| `enables` | 3,116 | 3.2% | 0.929 |
| `contains` | 2,610 | 2.7% | 0.946 |
| `produces` | 2,156 | 2.2% | 0.948 |
| `available_at` | 1,591 | 1.6% | 0.945 |
| `causes` | 1,383 | 1.4% | 0.929 |
| `part_of` | 1,164 | 1.2% | 0.942 |
| `used_for` | 1,111 | 1.1% | 0.932 |
| `offers` | 792 | 0.8% | 0.945 |
| `includes` | 789 | 0.8% | 0.947 |
| `used_in` | 744 | 0.8% | 0.934 |
| `supports` | 634 | 0.7% | 0.936 |
| `is_part_of` | 611 | 0.6% | 0.936 |
| `prevents` | 552 | 0.6% | 0.927 |
| `improves` | 512 | 0.5% | 0.927 |
| `made_of` | 468 | 0.5% | 0.959 |
| `instance_of` | 429 | 0.4% | 0.963 |
| `reduces` | 425 | 0.4% | 0.933 |
| `affects` | 350 | 0.4% | 0.923 |
| `removes` | 291 | 0.3% | 0.942 |
| `helps` | 265 | 0.3% | 0.922 |
| `member_of` | 264 | 0.3% | 0.979 |
| `suitable_for` | 244 | 0.3% | 0.928 |

## 6. Subject / Object types

### Top 20 subject_type

| type | count | % |
|---|---|---|
| `Product` | 54,470 | 56.1% |
| `Organization` | 15,064 | 15.5% |
| `Information` | 9,456 | 9.7% |
| `Person` | 6,948 | 7.2% |
| `Event` | 2,884 | 3.0% |
| `Location` | 2,299 | 2.4% |
| `Skill` | 1,542 | 1.6% |
| `CountryRegion` | 782 | 0.8% |
| `City` | 737 | 0.8% |
| `PersonType` | 626 | 0.6% |
| `URL` | 453 | 0.5% |
| `Structural` | 353 | 0.4% |
| `GPE` | 315 | 0.3% |
| `Number` | 181 | 0.2% |
| `ComputingProduct` | 149 | 0.2% |
| `Date` | 115 | 0.1% |
| `Temporal` | 111 | 0.1% |
| `Geographical` | 105 | 0.1% |
| `NaturalEvent` | 102 | 0.1% |
| `Currency` | 59 | 0.1% |

### Top 20 object_type

| type | count | % |
|---|---|---|
| `Product` | 31,629 | 32.6% |
| `Information` | 29,506 | 30.4% |
| `Location` | 5,330 | 5.5% |
| `Organization` | 4,810 | 5.0% |
| `Number` | 4,293 | 4.4% |
| `Person` | 2,302 | 2.4% |
| `Event` | 2,030 | 2.1% |
| `Percentage` | 1,855 | 1.9% |
| `CountryRegion` | 1,819 | 1.9% |
| `Skill` | 1,724 | 1.8% |
| `Currency` | 1,648 | 1.7% |
| `Duration` | 1,594 | 1.6% |
| `Structural` | 1,349 | 1.4% |
| `City` | 1,053 | 1.1% |
| `Date` | 847 | 0.9% |
| `URL` | 827 | 0.9% |
| `GPE` | 697 | 0.7% |
| `Dimension` | 421 | 0.4% |
| `Length` | 405 | 0.4% |
| `Temperature` | 341 | 0.4% |

### object_kind

| kind | count | % |
|---|---|---|
| `entity` | 54,412 | 56.0% |
| `literal` | 42,720 | 44.0% |

## 7. Predicate phrase analysis — RapidFuzz greedy clustering

Klastrowanie 3000 top unique predicate phrases używając
`fuzz.token_sort_ratio` z thresholdem 82.
Total clusters: **2234**

### Top 30 największych klastrów (po sumarycznej liczbie wystąpień)

| Cluster | Total occurrences | Members (top 5) |
|---|---|---|
| #0 | 1,437 | `wymaga` (1362) · `wymagają` (75) |
| #1 | 939 | `posiada` (850) · `posiadają` (89) |
| #3 | 821 | `oferuje` (649) · `oferują` (157) · `oferujemy` (15) |
| #2 | 740 | `zawiera` (663) · `zawierają` (77) |
| #4 | 594 | `składa się z` (538) · `składają się z` (37) · `składający się z` (8) · `składa się ze` (4) · `składa się w` (4) |
| #16 | 477 | `takie jak` (274) · `takich jak` (146) · `takimi jak` (12) · `taką jak` (11) · `takiej jak` (9) |
| #10 | 461 | `wykorzystuje` (334) · `wykorzystują` (66) · `wykorzystuje się` (18) · `wykorzystaj` (17) · `wykorzystując` (12) |
| #5 | 458 | `w` (458) |
| #8 | 417 | `numer pantone *` (353) · `ma numer pantone` (46) · `numer pantone` (10) · `ma numer pantone *` (5) · `numer pantone to` (3) |
| #7 | 393 | `kolor pantone*` (353) · `ma kolor pantone` (28) · `ma kolor pantone*` (5) · `kolor pantone` (4) · `kolor pantone to` (3) |
| #9 | 393 | `kod rgb` (346) · `ma kod rgb` (47) |
| #18 | 377 | `saturation (hsl)` (252) · `saturation (hsv)` (87) · `ma saturation (hsl)` (23) · `ma saturation (hsv)` (15) |
| #12 | 371 | `kod hex` (314) · `ma kod hex` (57) |
| #6 | 367 | `kolorral*` (359) · `ma kolorral*` (4) · `kolor ral` (4) |
| #13 | 367 | `obejmuje` (306) · `obejmują` (61) |
| #19 | 351 | `light (hsl)` (248) · `light (hsv)` (80) · `ma light (hsl)` (23) |
| #11 | 318 | `jest` (318) |
| #15 | 310 | `będziesz potrzebować` (286) · `będziemy potrzebować` (16) · `będziesz potrzebował` (8) |
| #35 | 305 | `składniki:` (125) · `składniki` (106) · `składnik` (65) · `składnikami:` (9) |
| #14 | 304 | `hue` (304) |
| #20 | 281 | `zapewnia` (229) · `zapewniają` (40) · `zapewni` (8) · `zapewnią` (4) |
| #17 | 264 | `można dodać` (261) · `można dodawać` (3) |
| #21 | 252 | `może zawierać` (219) · `mogą zawierać` (25) · `może wspierać` (8) |
| #29 | 243 | `znajduje się w` (145) · `znajduje się na` (47) · `znajdują się w` (33) · `znajdują się` (12) · `znajdujący się w` (6) |
| #32 | 238 | `wykonane z` (133) · `wykonana z` (38) · `wykonany z` (27) · `wykonane są z` (24) · `wykonane ze` (7) |
| #22 | 229 | `wartości cmyk` (214) · `ma wartości cmyk` (15) |
| #23 | 227 | `może prowadzić do` (209) · `mogą prowadzić do` (13) · `może wprowadzić` (5) |
| #31 | 210 | `może być` (134) · `może to być` (35) · `może być w` (28) · `może być na` (8) · `może być z` (5) |
| #24 | 189 | `ma` (189) |
| #49 | 180 | `można znaleźć w` (93) · `można znaleźć` (34) · `można znaleźć na` (32) · `możesz znaleźć w` (9) · `można znaleźć je w` (5) |

## 8. HDBSCAN clustering (TF-IDF char-ngrams)

Top 5000 phrases vectorized (TF-IDF char_wb 3-5 ngrams), HDBSCAN min_cluster_size=8.

- Total clusters: **75**
- Noise (label=-1): **3,513** (70.3%)
- Clustered: **1,487** (29.7%)

### Top 20 HDBSCAN clusters

| Label | Size | Sample phrases |
|---|---|---|
| 71 | 55 | `posiada` · `może posiadać` · `posiadają` · `posiada sekcję` · `posiada funkcję` |
| 74 | 50 | `dostępne w` · `udostępnia` · `dostępna na` · `dostępna w` · `dostępny w` |
| 2 | 46 | `ma` · `ilość` · `grubość` · `ma kod hex` · `ma kod rgb` |
| 35 | 44 | `może korzystać z` · `korzysta z` · `możesz skorzystać z` · `można wykorzystać` · `wykorzystanie` |
| 73 | 41 | `oferuje` · `oferują` · `oferuje opcję` · `oferuje dostęp do` · `oferują szeroki wybór` |
| 20 | 39 | `pomaga w` · `produkuje` · `wspomaga` · `mogą wspomagać` · `pomagają w` |
| 62 | 36 | `jest` · `jest w` · `jest to` · `to jest` · `jest symbolem` |
| 40 | 34 | `przechowywać w` · `można przechowywać w` · `przechowuj w` · `można przechowywać przez` · `należy przechowywać w` |
| 67 | 33 | `wymaga` · `wymagają` · `wymaga podania` · `wymaga ilości` · `wymaga procesora` |
| 68 | 33 | `zastosowanie` · `znajdują zastosowanie w` · `stosowany w` · `znajduje zastosowanie w` · `zastosowanie w` |
| 14 | 31 | `aktualna cena wynosi` · `cena wynosi` · `wynosi` · `czas trwania` · `wynosi około` |
| 33 | 30 | `umożliwia` · `umożliwiają` · `umożliwia tworzenie` · `umożliwia korzystanie z` · `umożliwia dostęp do` |
| 45 | 30 | `jest elementem` · `odgrywa kluczową rolę w` · `jest kluczowym elementem` · `są elementem` · `są nieodłącznym elementem` |
| 18 | 29 | `działa w` · `działa` · `ma działanie` · `działa od` · `działa na terenie` |
| 53 | 27 | `obejmuje` · `obejmują` · `objawy obejmują` · `obejmuje m.in.` · `obejmującego` |
| 65 | 26 | `kolorral*` · `kolor pantone*` · `kolor` · `kolor główny` · `ma kolor ral` |
| 25 | 26 | `takich jak` · `jest rodzajem` · `to rodzaj` · `w miastach takich jak` · `miast takich jak` |
| 23 | 26 | `składniki:` · `składniki` · `składnik` · `składniki to` · `składniki na danie to` |
| 36 | 25 | `wykorzystuje` · `wykorzystują` · `wykorzystuje technologię` · `wykorzystuje się` · `wykorzystując` |
| 59 | 25 | `może prowadzić do` · `wprowadza` · `prowadzi do` · `mogą prowadzić do` · `prowadzi` |

## 9. Decision summary

### A. Czy SPO ma sens utrzymać?

- **Confidence:** 99.9% trójek ma confidence ≥0.85 (top quality)
- **Mean triples/record:** 9.32 → znacząco bogata reprezentacja
- **Unique relation_types:** 2546 → schema bogaty, ale czy warto?

### B. Czy cap=8 jest uzasadnione?

**MARGINALNIE** — drop-off 0.012, sygnał słaby ale obecny.

### C. Klastrowanie — co mówi?

- RapidFuzz: **2234** unikalnych klastrów predicate_phrase z 3000 top phrases
- Top klaster zawiera **1,437** wystąpień (1.5% wszystkich triples)
- HDBSCAN: **75** klastrów (po TF-IDF char-ngrams)
- **Implikacja:** wysokie pokrycie przez kilka top klastrów = predicate_phrase można skompresować do enuma