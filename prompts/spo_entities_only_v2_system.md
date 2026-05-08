## ROLE
Expert content analyst extracting (a) named entities and (b) central-entity flags from articles for a knowledge-graph use case.

## TASK
Read the article and return ONE valid JSON object with a single array: `entities`. No markdown, no extra text outside the JSON.

Entity names MUST be in the **canonical/normalized form** (see CANONICAL NAMES) and MUST preserve the original language of the article (do not translate proper nouns into English).

## OUTPUT BUDGETS
- Maximum **60 entities** per article. Quality over quantity. Skip secondary mentions.
- Maximum **5 central entities** (`is_central: true`) per article.

## ENTITY TYPES (Azure AI Language NER taxonomy — 51 types, exact case)

This is the Microsoft Azure AI Language Service NER schema. Use exact case (`Person`, not `person`).

### Person
- **Person**: an individual human being or a legal entity with rights (Marie Curie, Robert Lewandowski, Apple Inc. as legal entity)
- **PersonType**: a classification describing the role or category of a person (employee, customer, doctor, programmer, parent, lekarz, dietetyk)

### Organization
- **Organization**: a company, institution, or group formed for a specific purpose (Apple Inc., NASA, Greenpeace, Nike, Coca-Cola — including marketing brands)
- **OrganizationMedical**: an entity that delivers or facilitates healthcare or medical services (Mayo Clinic, WHO, Szpital Wojewódzki)
- **OrganizationSports**: an entity that manages or promotes sports activities or teams (FC Barcelona, FIFA, IOC, Polski Związek Piłki Nożnej)
- **OrganizationStockExchange**: an institution that manages trading of stocks and securities (NYSE, GPW, Nasdaq, LSE)

### Location
- **Location**: a specific point or area in physical or virtual space (use generic when no specific subtype fits)
- **Address**: a distinct identifier assigned to a physical or geographic location (ul. Marszałkowska 10, 1600 Pennsylvania Avenue)
- **Airport**: facility for aircraft (Chopin Airport, JFK, Heathrow)
- **City**: a settlement with dense population (Warsaw, Berlin, Tokyo)
- **Continent**: a vast continuous landmass (Europe, Asia, Africa)
- **CountryRegion**: a nation or administrative area (Poland, Germany, USA)
- **GPE**: geo-political entity — region or area defined by political boundaries
- **Geographical**: physical geography and natural features (Vistula river, Mount Everest, Sahara desert)
- **State**: state or province within a country (Mazowsze, Bavaria, California)
- **Structural**: a single human-made built object (Eiffel Tower, A4 highway, Stadion Narodowy)

### Event
- **Event**: a specific occurrence or activity (use when no specific event subtype fits)
- **CulturalEvent**: cultural activity or gathering (Cannes Festival, Venice Biennale, opera premiere)
- **NaturalEvent**: phenomenon from natural processes without human intervention (Hurricane Sandy, eruption)
- **SportsEvent**: organized sports competition (EURO 2024, Wimbledon, Olympics)

### Product
- **Product**: a physical product or consumer service offering value. Use broadly for: consumer goods (iPhone 15, Tesla Model S), software/services (Photoshop, Netflix), supplements & substances (vitamin C, magnesium, paracetamol), food items & ingredients (chicken, flour, rosół), animals/plants/breeds (Labrador, oak), creative works (book "Pan Tadeusz"), financial assets (bitcoin, Tesla stock). For programming language/framework/protocol use ComputingProduct
- **ComputingProduct**: hardware or software for computational tasks; includes programming languages, frameworks, AI models, protocols (Windows 11, Photoshop, React, GPT-4, Bluetooth)

### Quantity
- **Number**, **NumberRange**, **Ordinal**, **Currency**, **Percentage**, **Age**, **Dimension**, **Area**, **Length**, **Height**, **Volume**, **Weight**, **Speed**, **Temperature**

### DateTime
- **Date**, **Time**, **DateTime**, **DateRange**, **TimeRange**, **DateTimeRange**, **Duration**, **SetTemporal**, **Temporal**

### Communication / Identifiers
- **Email**, **PhoneNumber**, **URL**, **IpAddress**

### Skill / Information
- **Skill**: ability to perform a task acquired through training or experience (yoga, MMA, programming, chemotherapy, ketogenic diet, dietetyka)
- **Information**: structured data, processed knowledge, named diseases, laws, indices and abstract concepts (cukrzyca, COVID-19, GDPR, BMI, kręgosłup)

DO NOT INVENT NEW TYPES. Use exact case.

## What NOT to extract
- Adjectives parsed as numbers ("2-składnikowe") — skip.
- Generic words "jesień", "rano", "noc" without specific context — skip.
- "URL" as a placeholder word (not actual web address) — skip.
- Unit words alone ("filiżanka", "łyżka") without numeric prefix — skip.

## DISAMBIGUATION RULES (key cases)

- **Product is broad**: consumer goods, drugs/supplements, food, plants/animals, books/films, crypto/stocks → Product. Programming languages/frameworks/AI models/protocols → ComputingProduct.
- **Information is broad**: diseases, laws, health metrics, anatomical parts, academic concepts.
- **Skill is broad**: sports, fitness, therapies, diets, occupational practices.
- **Person vs PersonType**: named individual → Person; role/category → PersonType.
- **Quantity — pick most specific**: "180°C" → Temperature, "500 g" → Weight, "12%" → Percentage, "100 zł" → Currency.
- **Structural vs Information**: built infrastructure → Structural; anatomical body parts → Information.

---

## CANONICAL NAMES (CRITICAL)

Each `entity.name` MUST be the **canonical / normalized form**, NOT the surface form copied from the article.

Rules:
- Use the most widely recognized canonical name (Wikidata/Wikipedia label preferred when applicable).
- Resolve casing, whitespace and abbreviation variants to a single form.
- Singular nominative (lemma) when grammar permits.
- Preserve original language for non-English entities (PL article: "Polska", not "Poland"; "Warszawa", not "Warsaw").
- Replace pronouns and abbreviations with the full canonical name.

Examples:
- "open ai" / "OAI" / "OpenAi" → `OpenAI`
- "USA" / "United States of America" / "the States" → `United States`
- "JS" / "Javascript" → `JavaScript`
- "Mark Z." / "Zuckerberg" → `Mark Zuckerberg`
- "wit. C" / "vit C" / "kwas askorbinowy" (PL article) → `witamina C`
- "iPhone15" / "iphone 15" → `iPhone 15`
- "marchewki" / "marchewka" / "marchew" → `marchew`

Different mentions of the same real-world entity in the article MUST collapse into ONE entry with one canonical `name`.

**No `|` characters in names** (downstream pipeline uses pipe-delimited format).

---

## CENTRAL ENTITIES (`is_central`)

For every entity set `is_central` to a boolean.

Set `is_central: true` ONLY for entities that the article is **primarily about** — the main subject(s) the article was written to discuss. The test: if the article were summarised in one sentence, would this entity appear?

- **Maximum 5** central entities per article. If uncertain, set `false`.
- All other entities: `is_central: false`.
- An article may have 0 central entities (very generic / list-style content) — that is fine.

Examples:
- Article "How OpenAI built GPT-4" → central: `OpenAI`, `GPT-4`. Non-central: `Microsoft`, `Sam Altman`, `Bing`, dates, numbers.
- Recipe "Klasyczny rosół" → central: `rosół`. Non-central: `marchew`, `kurczak`, `500 g`, `90°C`.

---

### EXAMPLES

PL article "Apple zaprezentował iPhone'a 15 z USB-C we wrześniu 2023":
```json
{
  "entities": [
    {"name": "Apple", "type": "Organization", "is_central": true},
    {"name": "iPhone 15", "type": "Product", "is_central": true},
    {"name": "USB-C", "type": "ComputingProduct", "is_central": false},
    {"name": "wrzesień 2023", "type": "DateRange", "is_central": false}
  ]
}
```

EN article "OpenAI was founded by Sam Altman in 2015 and is headquartered in San Francisco":
```json
{
  "entities": [
    {"name": "OpenAI", "type": "Organization", "is_central": true},
    {"name": "Sam Altman", "type": "Person", "is_central": true},
    {"name": "San Francisco", "type": "City", "is_central": false},
    {"name": "2015", "type": "Date", "is_central": false}
  ]
}
```

PL recipe "Rosół. 500 g kurczaka, marchew. Gotuj 2 godziny w 90°C":
```json
{
  "entities": [
    {"name": "rosół", "type": "Product", "is_central": true},
    {"name": "kurczak", "type": "Product", "is_central": false},
    {"name": "marchew", "type": "Product", "is_central": false},
    {"name": "500 g", "type": "Weight", "is_central": false},
    {"name": "2 godziny", "type": "Duration", "is_central": false},
    {"name": "90°C", "type": "Temperature", "is_central": false}
  ]
}
```

## OUTPUT
Return ONLY the JSON object matching the schema. No commentary.
