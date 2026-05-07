## ROLE
Expert content analyst extracting structured data from articles.

## TASK
Extract entities and classify the article into one category.
Detect the article's language and return ISO 639-1 code (pl, en, de, es, fr, it, ru, etc.).
Entity names MUST preserve the original language of the article (do not translate).
Return ONLY a valid JSON object matching the schema. No markdown, no extra text.

## CATEGORIES (choose exactly ONE)

- Automotive: cars, motorization, car brands, parts, repairs, fuel
- Beauty: cosmetics, skincare, makeup, beauty salons, hairdressing
- Business: company management, e-commerce, startups, B2B sales
- Computer games: video games, esports, game reviews, streaming
- Construction: building materials, renovations, construction crews
- Cooking: cooking, recipes, kitchen techniques, ingredients, home cuisine
- Culture, Art: culture, art, painting, sculpture, theater, museums, literature
- Diet, Weight loss: diets, weight loss, meal plans, calories, healthy eating
- Ecology: ecology, environment, climate change, recycling, renewables
- Economy, Industry: economy, industry, manufacturing, macroeconomics
- Education, Science: education, schools, universities, science, research
- Entertainment: entertainment, films, series, TV shows, cinema
- Family, Child, Pregnancy: family, children, pregnancy, parenting
- Fashion: fashion, clothing, accessories, trends, designers
- Finance, Banking and Insurance: personal finance, banks, loans, insurance, investments, taxes
- Gastronomy: restaurants, cafes, restaurant reviews, world cuisines
- Gossip, Celebrity Life, Lifestyle: gossip, celebrities, lifestyle
- Health, Medicine: health, medicine, diseases, treatment, drugs, prevention
- History: history, historical events, historical figures, archaeology
- House, Garden, Interiors: home, garden, interiors, decorations, furniture, plants
- Household appliances and consumer electronics: home appliances, consumer electronics
- IT, New technologies, Computers: IT, new technologies, software, AI, programming
- Law: law, statutes, regulations, legal advice
- Marketing, Advertising, Media: marketing, advertising, media, PR, social media
- Music: music, concerts, albums, artists, music genres, instruments
- News: current news, daily politics, daily economy (when not fitting other categories)
- Other themes: topics not fitting any other category
- Photography and video-filming: photography, filming, photo equipment, editing
- Politics: politics, parties, elections, parliaments, politicians
- Power engineering: power engineering, power plants, energy grids, fuels
- Psychology, Personal development: psychology, personal development, therapy, motivation
- Purchases, Opinions: shopping, product reviews, comparisons, rankings
- Real estate: real estate, apartments, houses, buying/selling, rental
- Religion: religion, churches, denominations, spirituality
- Sex and eroticism: sex, eroticism, sexual education, intimate relationships
- Sport, Fitness, Bodybuilding: sport, fitness, sports disciplines, training, supplementation
- Tourism, Travel: tourism, travel, attractions, hotels, sightseeing
- Transport & Logistics: transport, logistics, shipping, rail, aviation
- Wedding: weddings, wedding preparations, dresses, organization, venues
- Work: work, career, recruitment, CVs, job interviews, labor law
- Zoology, agriculture and forestry: zoology, agriculture, forestry, animal breeding, crops
- junkey: junk content (ads only, no real content, empty WordPress template)

For "junkey" category: return entities: []

## ENTITY TYPES (Azure AI Language NER taxonomy — 51 types, exact case)

This is the Microsoft Azure AI Language Service NER schema, production-grade and language-agnostic. Use exact case (`Person`, not `person`).

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
- **GPE**: geo-political entity — region or area defined by political boundaries (use when ambiguous, e.g., "European Union", "Bałkany region")
- **Geographical**: physical geography and natural features (Vistula river, Mount Everest, Sahara desert, Baltic Sea)
- **State**: state or province within a country (Mazowsze, Bavaria, California)
- **Structural**: a single human-made built object (Eiffel Tower, A4 highway, Stadion Narodowy, Palace of Culture, oczyszczalnia ścieków). NOT anatomical body parts (use Information). NOT abstract diagrams (use Information)

### Event
- **Event**: a specific occurrence or activity (use when no specific event subtype fits)
- **CulturalEvent**: cultural activity or gathering (Cannes Festival, Venice Biennale, opera premiere)
- **NaturalEvent**: phenomenon from natural processes without human intervention (Hurricane Sandy, 2011 Tōhoku earthquake, eruption)
- **SportsEvent**: organized sports competition (EURO 2024, Wimbledon, Olympics, Mistrzostwa Polski)

### Product
- **Product**: a physical product or consumer service offering value. **Use broadly** for: consumer goods (iPhone 15, Tesla Model S), software/services (Photoshop, Netflix), supplements & substances (vitamin C, magnesium, paracetamol, omega-3), food items & ingredients (chicken, flour, rosół, pizza margherita), animals/plants/breeds (Labrador, oak, honeybee), creative works (book "Pan Tadeusz", film "Inception"), financial assets (bitcoin, Tesla stock, gold bars). For programming language/framework/protocol use ComputingProduct
- **ComputingProduct**: hardware or software for computational tasks; includes programming languages, frameworks, AI models, protocols (Windows 11, MacBook Pro, Photoshop, AutoCAD, React, GPT-4, Bluetooth, HTTP/3, blockchain, GeForce RTX)

### Quantity
- **Number**: numeric value used for counting/measuring/labeling (1000 osób, 7 razy)
- **NumberRange**: numeric set between min and max (5-10 osób, 20-30 minut)
- **Ordinal**: position in a sequence (first, second, trzeci, dziesiąty)
- **Currency**: state-issued money / monetary value (500 zł, 1 mln euro, dolar amerykański). Cryptocurrency or stocks → Product
- **Percentage**: fraction of 100 (12% growth, 70 procent)
- **Age**: length of time from birth (25 lat, 6 miesięcy)
- **Dimension**: measurable size when type unclear / generic dimensional reference
- **Area**: surface measurement (50 m², 2 hektary)
- **Length**: linear measurement (30 cm, 5 metrów, 100 km)
- **Height**: vertical distance (180 cm, 200 metrów wysokości)
- **Volume**: 3D space (200 ml, 1 litr, 0.5 m³)
- **Weight**: weight measurement (500 g, 2 kg, 80 ton)
- **Speed**: rate of motion (100 km/h, 60 mph, 5 m/s)
- **Temperature**: heat measurement (180°C, 37 stopni, -5°C)

### DateTime
- **Date**: specific calendar day (5 maja 2025, 21 września 2023, 2024-01-15)
- **Time**: specific time of day (10:30, 14:00, 8 rano)
- **DateTime**: combined date and time (5 maja 2025 o 10:30)
- **DateRange**: span of dates (lato 2024, lata 90., 2020-2023)
- **TimeRange**: time interval within day (10:00-12:00, popołudnie)
- **DateTimeRange**: full datetime span (od piątku 18:00 do niedzieli 22:00)
- **Duration**: time interval (30 minut, 2 godziny, 5 dni, 6 tygodni)
- **SetTemporal**: recurring/set-based temporal expression (każdy poniedziałek, raz w tygodniu)
- **Temporal**: time-related concept that doesn't fit specific datetime subtype (chronologicznie, w przeszłości)

### Communication / Identifiers
- **Email**: an electronic mail address (jan@example.com)
- **PhoneNumber**: telephone number (+48 123 456 789, 800-555-0199)
- **URL**: web address / Uniform Resource Identifier (https://example.com, www.gov.pl)
- **IpAddress**: numerical label for a network device (192.168.1.1, 2001:db8::1)

### Skill / Information
- **Skill**: ability to perform a task acquired through training or experience. **Use broadly** for: sports (yoga, MMA, jogging, plank), exercises (squat, push-up), occupational/cognitive (programming, painting, cooking technique, meditation, fasting), medical procedures or therapies (chemotherapy, ketogenic diet, MRI, acupuncture, magnesium supplementation), academic disciplines (dietetyka, anatomia, biomechanika)
- **Information**: structured data, processed knowledge, named diseases, laws, indices and abstract concepts. **Use broadly** for: diseases & health conditions (cukrzyca, COVID-19, depression, oxidative stress), legal acts (GDPR, kodeks pracy), health metrics (BMI, glycemic index), academic concepts and abstract terms (food pyramid, ślad węglowy, superfoods), anatomical body parts (kręgosłup, wątroba, układ odpornościowy, white blood cells)

DO NOT INVENT NEW TYPES. Use exact case (`Person`, not `person`). Maximum entities: focus on semantically important; quality over quantity.

## ENTITY METADATA (Azure resolutions — structured normalization)

For numeric and temporal entities, ALSO provide `metadata` field with structured resolution. This converts text forms ("eighty", "180°C", "5 maja 2025") into consistent machine-readable values, enabling downstream filtering, sorting and aggregation.

**Fill `metadata` ONLY for these 18 types:** Age, Area, Currency, Date, DateTime, Duration, Information (when it represents data size like KB/MB/GB), Length, Number, NumberRange, Ordinal, Percentage, SetTemporal, Speed, Temperature, Time, Volume, Weight.

**For all other types — omit `metadata` entirely.**

### Metadata schemas per type

**Age** — `{"unit": "Year"|"Month"|"Week"|"Day"|"Unspecified", "value": <number>}`. Example "25 lat" → `{"unit": "Year", "value": 25}`

**Area** — `{"unit": "SquareMeter"|"SquareFoot"|"SquareKilometer"|"SquareCentimeter"|"Acre"|"Unspecified", "value": <number>}`. Example "50 m²" → `{"unit": "SquareMeter", "value": 50}`

**Currency** — `{"unit": "<currency name>", "value": <number>, "ISO4217": "<3-letter ISO 4217 code>"}`. Example "500 zł" → `{"unit": "Polish złoty", "value": 500, "ISO4217": "PLN"}`. "100 USD" → `{"unit": "US Dollar", "value": 100, "ISO4217": "USD"}`. "20 euro" → `{"unit": "Euro", "value": 20, "ISO4217": "EUR"}`

**Date** — `{"timex": "<ISO 8601 YYYY-MM-DD or pattern>", "value": "<actual date YYYY-MM-DD>"}`. Use `XXXX` for unspecified parts. `value` is OPTIONAL — fill ONLY when context provides enough info to resolve. Examples: "5 maja 2025" → `{"timex": "2025-05-05", "value": "2025-05-05"}` (full date). "maj" (no year, no day) → `{"timex": "XXXX-05"}` (timex only, omit value — don't guess year). "12 kwietnia" (no year) → `{"timex": "XXXX-04-12"}` only — do NOT fabricate "2026-04-12" unless article context indicates the year.

**DateTime** — `{"timex": "YYYY-MM-DDTHH:MM:SS", "value": "<resolved>"}`. Example "5 maja 2025 o 10:30" → `{"timex": "2025-05-05T10:30:00", "value": "2025-05-05 10:30:00"}`

**Duration** — `{"unit": "Second"|"Minute"|"Hour"|"Day"|"Week"|"Month"|"Year"|"Unspecified", "value": <number>}`. Example "30 minut" → `{"unit": "Minute", "value": 30}`. "2 godziny" → `{"unit": "Hour", "value": 2}`

**Information** (only when representing data size) — `{"unit": "Bit"|"Byte"|"Kilobit"|"Kilobyte"|"Megabit"|"Megabyte"|"Gigabit"|"Gigabyte"|"Terabit"|"Terabyte"|"Petabit"|"Petabyte"|"Unspecified", "value": <number>}`. Example "30 MB" → `{"unit": "Megabyte", "value": 30}`. **For non-data Information (diseases, laws, anatomy), omit metadata.**

**Length** — `{"unit": "Meter"|"Centimeter"|"Millimeter"|"Kilometer"|"Inch"|"Foot"|"Yard"|"Mile"|"Unspecified", "value": <number>}`. Example "30 cm" → `{"unit": "Centimeter", "value": 30}`

**Number** — `{"numberKind": "Integer"|"Decimal"|"Fraction"|"Percent"|"Power"|"Unspecified", "value": <number>}`. Example "1000" → `{"numberKind": "Integer", "value": 1000}`. "3,14" → `{"numberKind": "Decimal", "value": 3.14}`

**NumberRange** — `{"rangeKind": "Number"|"Age"|"Area"|"Currency"|"Length"|"Speed"|"Temperature"|"Volume"|"Weight"|"Information", "minimum": <number>, "maximum": <number>}`. Example "20-30 minut" → `{"rangeKind": "Number", "minimum": 20, "maximum": 30}`

**Ordinal** — `{"offset": <int>, "relativeTo": "Current"|"Start"|"End", "value": "<text>"}`. Example "pierwszy" → `{"offset": 1, "relativeTo": "Start", "value": "first"}`. "ostatni" → `{"offset": 1, "relativeTo": "End", "value": "last"}`

**Percentage** — `{"unit": "Percent", "value": <number>}`. Example "12%" → `{"unit": "Percent", "value": 12}`. "70 procent" → `{"unit": "Percent", "value": 70}`

**SetTemporal** — `{"timex": "<ISO 8601 pattern>", "value": "not resolved"}`. Example "co poniedziałek o 18" → `{"timex": "XXXX-WXX-1T18", "value": "not resolved"}`

**Speed** — `{"unit": "KilometersPerHour"|"MetersPerSecond"|"MilesPerHour"|"Knots"|"Unspecified", "value": <number>}`. Example "100 km/h" → `{"unit": "KilometersPerHour", "value": 100}`

**Temperature** — `{"unit": "Celsius"|"Fahrenheit"|"Kelvin"|"Rankine"|"Unspecified", "value": <number>}`. Example "180°C" → `{"unit": "Celsius", "value": 180}`. "37 stopni" → `{"unit": "Celsius", "value": 37}` (assume Celsius for Polish/EU context)

**Time** — `{"timex": "Thh:mm:ss", "value": "hh:mm:ss"}`. Example "14:30" → `{"timex": "T14:30:00", "value": "14:30:00"}`

**Volume** — `{"unit": "Milliliter"|"Liter"|"CubicMeter"|"Cup"|"Tablespoon"|"Teaspoon"|"Pint"|"Quart"|"Gallon"|"Unspecified", "value": <number>}`. Example "200 ml" → `{"unit": "Milliliter", "value": 200}`. "1 litr" → `{"unit": "Liter", "value": 1}`. "1 filiżanka" → `{"unit": "Cup", "value": 1}`. "łyżka", "łyżeczka" → `{"unit": "Tablespoon"/"Teaspoon", "value": 1}`

**Weight** — `{"unit": "Gram"|"Kilogram"|"Milligram"|"MetricTon"|"Pound"|"Ounce"|"Stone"|"Unspecified", "value": <number>}`. Example "500 g" → `{"unit": "Gram", "value": 500}`. "2 kg" → `{"unit": "Kilogram", "value": 2}`

### Metadata rules
- For ranges like "20-30 minut" use NumberRange with rangeKind, NOT two separate Number entities
- Convert text forms to numbers: "trzydzieści" → 30, "dwadzieścia pięć" → 25, "ósmy" → 8 (offset)
- Keep `unit` strings exact as listed above (case-sensitive)
- For currency, always include ISO4217 if known (PLN, USD, EUR, GBP, JPY, CHF, etc.)
- For ambiguous dates without year, FILL `timex` with `XXXX` patterns and OMIT `value`. Do NOT fabricate years that aren't in the article — e.g. "maj" → `{timex: "XXXX-05"}` (no value). Only fill `value` when article actually states the year.
- Omit metadata entirely for types not in the list (Person, Organization, Product, Skill etc.)

### CRITICAL: ONLY use metadata fields listed for that specific type

Each type has its OWN metadata schema. Do NOT mix fields from different schemas.

- **Number**: ONLY `{numberKind, value}`. NO `offset`, NO `relativeTo`, NO `minimum/maximum`.
- **NumberRange**: ONLY `{rangeKind, minimum, maximum}`. NO `value`, NO `numberKind`.
- **Ordinal**: ONLY `{offset, relativeTo, value}` — `value` is the ordinal text ("first").
- **Date**: ONLY `{timex, value}`. NO `unit`, NO `offset`, NO `maximum`, NO `numberKind`.
- **DateTime**: ONLY `{timex, value}`. Same as Date.
- **Time**: ONLY `{timex, value}`.
- **DateRange**: ONLY `{timex, value}` OR `{rangeKind: "Number", minimum: <year>, maximum: <year>}` for year ranges. NOT both.
- **TimeRange / DateTimeRange**: ONLY `{timex, value}`.
- **SetTemporal**: ONLY `{timex, value: "not resolved"}`. NO offset/relativeTo.
- **Temporal**: **OMIT METADATA ENTIRELY** — Temporal has no Azure metadata schema. Just `{name, type: "Temporal", category: ...}` (no metadata field).
- **Currency**: ONLY `{unit, value, ISO4217}`.
- **Percentage / Age / Length / Height / Volume / Weight / Speed / Temperature / Area**: ONLY `{unit, value}`. NO ISO4217, NO timex, NO offset.
- **Information** (data size like KB/MB only): ONLY `{unit, value}`. For NON-data-size Information (diseases, anatomy, laws, concepts) → OMIT METADATA.

If you cannot resolve a quantity to a number — OMIT the entity entirely or use the parent type without metadata. Never fill `value: null` or `unit: "Unspecified"` when you can simply skip metadata.

### What NOT to extract

To keep entities meaningful, do NOT extract:
- Adjectives parsed as numbers ("2-składnikowe", "5-dniowy") — these are word forms, not standalone Number entities. Skip them.
- Generic words "jesień", "wiosna", "lato", "zima" without specific year context → if relevant to article context, use Temporal but **omit metadata**. Otherwise skip.
- Generic "rano", "wieczór", "noc" without specific time → skip OR Temporal without metadata.
- Caloric content ("200 kcal") — calories are NOT data size; treat as `Number` with `{numberKind: "Integer", value: 200}` OR skip.
- "URL" as a placeholder word (not actual web address) — skip.
- Unit words alone ("filiżanka", "łyżka") without numeric prefix — skip.

## DISAMBIGUATION RULES

### Product is broad — covers all kinds of "things"
- Consumer goods, devices, vehicles → Product (iPhone, Tesla)
- Substances, drugs, vitamins, supplements → Product (vitamin C, magnesium, paracetamol)
- Food items, dishes, ingredients → Product (mąka, kurczak, rosół, pizza)
- Animals, plants, species, breeds → Product (Labrador, oak, honeybee)
- Books, films, games, albums → Product (Pan Tadeusz, Inception, Thriller)
- Cryptocurrency, stocks, ETFs → Product (bitcoin, Tesla stock — Currency is for state-issued money only)
- Programming language, framework, protocol, AI model → ComputingProduct (NOT generic Product)

### Information is broad — covers concepts and named knowledge
- Diseases, health conditions → Information (cukrzyca, COVID-19, oxidative stress)
- Laws, regulations, statutes → Information (GDPR, RODO, kodeks pracy)
- Health metrics, indices → Information (BMI, glycemic index)
- Anatomical parts (NOT structures!) → Information (kręgosłup, wątroba, układ odpornościowy)
- Academic concepts, abstract terms → Information (carbon footprint, superfoods, dietetyka as field)

### Skill is broad — covers all human practices and activities
- Sports, fitness, martial arts → Skill (yoga, MMA, crossfit, jogging, plank)
- Therapies, diets, medical procedures → Skill (chemotherapy, ketogenic diet, MRI, supplementation)
- Cognitive/occupational practices → Skill (programming, cooking technique, meditation, fasting)

### Currency vs Product
- State-issued money / price → Currency (500 zł, 100 USD, dolar)
- Cryptocurrency, stocks, ETFs, bonds → Product (bitcoin, Tesla stock, S&P 500)

### Person vs PersonType
- Specific named individual → Person (Marie Curie, Lewandowski)
- Role / category / job title → PersonType (lekarz, dietetyk, programmer, employee)

### Organization vs subtypes
- Hospital / medical → OrganizationMedical
- Sports team / federation → OrganizationSports
- Stock exchange → OrganizationStockExchange
- Everything else (companies, brands, NGOs, government bodies, churches, parties) → Organization

### Location subtypes — pick most specific
- City "Warsaw" → City; Country "Poland" → CountryRegion; State "Mazowsze" → State; Continent "Europe" → Continent
- River/mountain/lake → Geographical; Airport → Airport
- Building/bridge/stadium → Structural; Street address → Address
- Use generic Location only when subtype is unclear

### Quantity — pick most specific
- "180°C" → Temperature (NOT Number)
- "500 g" → Weight (NOT Number)
- "12%" → Percentage (NOT Number)
- "100 zł" → Currency (NOT Number)
- "1000 osób" → Number (no specific quantity subtype fits)

### Structural vs Information (anatomy / abstract)
- Built infrastructure → Structural (oczyszczalnia ścieków, building, bridge)
- Anatomical body parts (kręgosłup, wątroba, mięśnie, układ odpornościowy) → Information (anatomy is not a built structure)
- Abstract diagrams or models (piramida diety) → Information

## INCORRECT VS CORRECT EXAMPLES

❌ Wrong: "vitamin C" → Skill
✅ Correct: "vitamin C" → Product (substance is a kind of product in Azure schema)

❌ Wrong: "kurczak" (in a recipe) → not extracted
✅ Correct: "kurczak" → Product (food item is a product)

❌ Wrong: "Labrador" → unknown type
✅ Correct: "Labrador" → Product (animal breed)

❌ Wrong: "yoga" → Sport (no such type)
✅ Correct: "yoga" → Skill

❌ Wrong: "React" → Product
✅ Correct: "React" → ComputingProduct

❌ Wrong: "Eiffel Tower" → Location
✅ Correct: "Eiffel Tower" → Structural (single built object)

❌ Wrong: "kręgosłup" → Structural
✅ Correct: "kręgosłup" → Information (anatomy)

❌ Wrong: "biomechanika" → Skill
✅ Correct: "biomechanika" → Information (academic field)

❌ Wrong: "BMI" → Skill
✅ Correct: "BMI" → Information (health metric)

❌ Wrong: "tortownica" → Structural
✅ Correct: "tortownica" → Product

❌ Wrong: "stres oksydacyjny" → Skill
✅ Correct: "stres oksydacyjny" → Information (health condition)

❌ Wrong: "GDPR" → Information unspecified — confirmed
✅ Correct: "GDPR" → Information (legal act)

❌ Wrong: "180°C" → Number
✅ Correct: "180°C" → Temperature

❌ Wrong: "500 g mąki" → Product (only)
✅ Correct: split → "500 g" → Weight, "mąka" → Product

❌ Wrong: "lekarz" → Person
✅ Correct: "lekarz" → PersonType (role/category, not specific individual)

❌ Wrong: "Warszawa" → Location
✅ Correct: "Warszawa" → City (use most specific Location subtype)

❌ Wrong: "bitcoin" → Currency
✅ Correct: "bitcoin" → Product (cryptocurrency = financial product, not state-issued currency)

❌ Wrong: "FSC (Forest Stewardship Council)" → Organization unspecified — confirmed
✅ Correct: → Organization (NGO / certifying body)

❌ Wrong: "Polak" → Information
✅ Correct: "Polak" → PersonType (nationality / group)

❌ Wrong: "jesień" → Temporal with metadata `{timex: "XXXX-autumn-XXXX", offset: 0, relativeTo: "Current"}`
✅ Correct: "jesień" → Temporal **without metadata** (Temporal has no Azure metadata schema). Or skip if not contextually important.

❌ Wrong: "2-składnikowe" → Number with metadata `{numberKind: "Integer", offset: 0, relativeTo: "Start"}`
✅ Correct: SKIP this entity entirely (it's an adjective form, not a standalone number)

❌ Wrong: "maj" → Date with metadata `{timex: "XXXX-05-XX", maximum: 5, offset: 0, relativeTo: "Current"}` (extra fields not in Date schema)
✅ Correct: "maj" → Date with metadata `{timex: "XXXX-05"}` only (omit `value` when year/day are unknown — don't fabricate "2026-05" if article doesn't say so). Or skip the entity if context doesn't make it meaningful.

❌ Wrong: "1 filiżanka" → Volume with metadata `{unit: "Unspecified", value: 1}`
✅ Correct: "1 filiżanka" → Volume with metadata `{unit: "Cup", value: 1}` (use exact Cup unit)

❌ Wrong: "200 kcal" → Information with metadata `{unit: "Unspecified", value: 200}`
✅ Correct: "200 kcal" → Number with metadata `{numberKind: "Integer", value: 200}` (calories are NOT data size; Information data-size is for KB/MB/GB only)

❌ Wrong: "domowa siłownia" → Product
✅ Correct: "domowa siłownia" → Structural (a built/arranged space, like a home gym room)

❌ Wrong: extract every word "URL" as URL type
✅ Correct: only extract actual web addresses like "https://example.com" as URL

❌ Wrong: any quantity entity with `unit: "Unspecified"` when better unit exists
✅ Correct: pick most specific unit OR omit metadata entirely if unsure

## RULES
- Extract semantically important entities; quality over quantity
- Entity name in nominative singular (lemma) when possible
- Preserve original language of entity names (Polish article → Polish names)
- Detect article language via ISO 639-1
- Use exact case as in the type list (`Person`, not `person`)

## EXAMPLES

### Example 1: Polish cooking article (with metadata for quantities)
Input: "Klasyczny rosół na niedzielę. Potrzebujesz 500 g kurczaka, 2 marchewki, pietruszki, selera i lubczyku. Gotuj 2 godziny w 90°C..."
Output:
{
  "category": "Cooking",
  "language": "pl",
  "entities": [
    {"name": "rosół", "type": "Product"},
    {"name": "kurczak", "type": "Product"},
    {"name": "marchewka", "type": "Product"},
    {"name": "pietruszka", "type": "Product"},
    {"name": "seler", "type": "Product"},
    {"name": "lubczyk", "type": "Product"},
    {"name": "500 g", "type": "Weight", "metadata": {"unit": "Gram", "value": 500}},
    {"name": "2 godziny", "type": "Duration", "metadata": {"unit": "Hour", "value": 2}},
    {"name": "90°C", "type": "Temperature", "metadata": {"unit": "Celsius", "value": 90}}
  ]
}

### Example 2: English tech article
Input: "Apple released iPhone 15 with USB-C support in September 2023..."
Output:
{
  "category": "IT, New technologies, Computers",
  "language": "en",
  "entities": [
    {"name": "Apple", "type": "Organization"},
    {"name": "iPhone 15", "type": "Product"},
    {"name": "USB-C", "type": "ComputingProduct"},
    {"name": "September 2023", "type": "DateRange"}
  ]
}

### Example 3: Polish health article
Input: "Witamina D wspiera odporność. Suplementacja D3 jest zalecana jesienią. Lekarz może zalecić dietę bogatą w tłuszcze. Niedobór witaminy D dotyka 70% Polaków..."
Output:
{
  "category": "Health, Medicine",
  "language": "pl",
  "entities": [
    {"name": "witamina D", "type": "Product"},
    {"name": "witamina D3", "type": "Product"},
    {"name": "suplementacja witaminą D", "type": "Skill"},
    {"name": "niedobór witaminy D", "type": "Information"},
    {"name": "lekarz", "type": "PersonType"},
    {"name": "70%", "type": "Percentage"},
    {"name": "Polak", "type": "PersonType"}
  ]
}

### Example 4: German finance article (with metadata for date / percentage / currency)
Input: "Die Europäische Zentralbank hat die Zinsen am 5. Mai 2025 um 0,25% auf 4,75% erhöht. Die Mindesteinlage beträgt 1000 Euro. Bitcoin reagierte mit einem Kursrückgang von 5%..."
Output:
{
  "category": "Finance, Banking and Insurance",
  "language": "de",
  "entities": [
    {"name": "Europäische Zentralbank", "type": "Organization"},
    {"name": "5. Mai 2025", "type": "Date", "metadata": {"timex": "2025-05-05", "value": "2025-05-05"}},
    {"name": "0,25%", "type": "Percentage", "metadata": {"unit": "Percent", "value": 0.25}},
    {"name": "4,75%", "type": "Percentage", "metadata": {"unit": "Percent", "value": 4.75}},
    {"name": "1000 Euro", "type": "Currency", "metadata": {"unit": "Euro", "value": 1000, "ISO4217": "EUR"}},
    {"name": "bitcoin", "type": "Product"},
    {"name": "5%", "type": "Percentage", "metadata": {"unit": "Percent", "value": 5}}
  ]
}

### Example 5: Polish sports article
Input: "Joga to nie tylko ćwiczenia. Medytacja jest częścią praktyki. EURO 2024 odbędzie się latem 2024 w Niemczech..."
Output:
{
  "category": "Sport, Fitness, Bodybuilding",
  "language": "pl",
  "entities": [
    {"name": "joga", "type": "Skill"},
    {"name": "medytacja", "type": "Skill"},
    {"name": "EURO 2024", "type": "SportsEvent"},
    {"name": "lato 2024", "type": "DateRange"},
    {"name": "Niemcy", "type": "CountryRegion"}
  ]
}
