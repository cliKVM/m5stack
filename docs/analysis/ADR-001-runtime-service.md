# ADR-001 — profil runtime-service dla kontrolera Androida

Data: 2026-11-21. Status: przyjęta decyzja projektowa.

## Kontekst

clikVM wykonuje ograniczony katalog operacji Androida, używając planu LLM,
obserwacji i lease. Nie jest agentem zmieniającym repozytoria, piszącym
własne umiejętności ani uruchamiającym autonomiczne naprawy kodu.

Początkowo sprawdzono `agent-executor`. Prawdziwe polecenie Wellmana
zarejestrowało również `repair-lifecycle`, `skills`, `agent` oraz wyższe
wymagania niezależnego Validation Attestation. Ten profil rozszerzał zakres
projektu poza żądane sterowanie telefonem. Pierwotne wyniki zachowano w
`initial-wellman-registration.json`, `initial-wellman-check.json` i
`initial-agent-requirements.json` jako ślad decyzji.

## Decyzja

Wybrano `runtime-service` wraz z jawnymi wymaganiami POA, Logs, LLM i SemVer.
Usunięto wyłącznie nowo utworzoną błędną rejestrację z tego projektu i
odtworzono ją rzeczywistym Wellmanem. Nie zmieniano istniejącego repozytorium
użytkownika ani zewnętrznych standardów. Wygenerowane wymagania są listą
celów adopcji, a nie uzyskanymi poziomami zgodności.

## Skutki

- Katalog `poa.operation/v1` i proces `poa.process/v1` waliduje oficjalny
  JSON Schema. Nie dodajemy niedozwolonych pól dla zadowolenia starszego checkera.
- Audyt Logs przechodzi również rzeczywisty upstream validator.
- OQLLM jest rzeczywistym transportem HTTPS aplikacji. Nie dopisujemy MCP lub
  Subactor-first, których runtime nie realizuje.
- Pełna mediacja floty i niezależna autoryzacja pozostają oddzielną adopcją.
  Lokalna sesja/lease aplikacji nie jest certyfikatem tej infrastruktury.
- Raporty pokazują konkretne niespełnione wymagania, obok przechodzących
  testów kontraktowych. Nie ukrywamy niezerowego wyniku `wellman check`.
