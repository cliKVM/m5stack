---
{
  "schema": "wellmanifest.docs/document/v1",
  "id": "clikvm-wellmanifest-adoption",
  "kind": "information",
  "version": 1,
  "title": "Adopcja Wellmanifest w clikVM/m5stack",
  "status": "draft",
  "owner": "clikVM/m5stack",
  "scope": "repository",
  "created": "2026-11-21",
  "updated": "2026-11-21",
  "review_after": "2026-12-21",
  "evidence": [".governance/standards.lock.json", "docs/analysis/audit-conformance.json"]
}
---

# Adopcja Wellmanifest

Projekt jest aplikacją wykonującą ograniczone operacje na urządzeniach przez
ADB, connector desktop, RFB i skonfigurowane API. Używa
kontraktów Wellmanifest jako ADOPT; właścicielem kodu runtime jest
`clikVM/m5stack`. Nie kopiuje silnika Wellman do firmware ani serwera.

## Co rzeczywiście zostało zweryfikowane

1. `wellman 0.23.2` uruchomiono z przypiętego źródła. Polecenie `adopt
   runtime-service` wygenerowało manifest i metadane Docs. `adopt auto`
   zarejestrowało wymagania. Ten wynik ma `conformance: unverified`.
2. Katalog 0.2.0 zawiera osiem typów operacji i 15 rekordów: siedem URI
   Androida zachowanych dla 0.1.0 oraz osiem ogólnych URI urządzenia.
   Wygenerowano go z rzeczywistych modeli Pydantic.
   Każdy wpis przechodzi **oficjalny `poa.operation/v1` JSON Schema** i wiąże
   się hashem z `gateway/clikvm/models.py`.
3. Opisy cyklu obserwacja–akcja–weryfikacja dla Androida oraz wielu
   adapterów przechodzą oficjalny **`poa.process/v1` JSON Schema**. Stary
   proces pozostaje w `.governance/poa.json`, nowy — w
   `.governance/poa-multidevice.json`.
4. Producent audytu przechodzi **`wellmanifest.logs/event/v1`**. Reprezentatywne
   zdarzenie zweryfikowano również rzeczywistą funkcją upstream
   `standard.logs_events.validate_event`, nie tylko lokalnym walidatorem.
5. Testy negatywne sprawdzają zmianę treści po obliczeniu hasha, dodatkowe
   pola, niewłaściwą sekwencję, błędny czas i wykluczone wartości JSON.
6. Odrębna weryfikacja online potwierdziła SHA-256 wszystkich dziewięciu
   przypiętych plików źródłowych. Kontrola offline ma pięć zaliczonych bramek,
   a testy producenta audytu — cztery zaliczone przypadki.

Te dowody dotyczą kontraktów i wykonanych testów. Nie potwierdzają działania
USB na fizycznej płytce, instalacji na telefonie, pełnej mediacji floty,
niezależnego zaufanego Validatora ani autoryzacji publikacji.

## Źródła i piny

Maszynowy wykaz zawiera [standards.lock.json](../.governance/standards.lock.json).
Każde skopiowane pole kontraktu ma źródłowy commit, ścieżkę, hash źródła i hash
lokalnego pliku. Jest to format **`clikvm.standards-lock/v1`**, a nie
podszywający się pod upstream certyfikat `wellman`.

| Źródło | Zastosowanie |
| --- | --- |
| [Wellman](https://github.com/wellmanifest/wellman/tree/e8946f97c5a28e4162c4a5a91e19e2bab2713925) | Rejestr wymagań, manifest i raport zaimplementowanych checkerów |
| [POA](https://github.com/wellmanifest/poa/tree/cba52778fcc2d942fe879bc71b13695368c38647) | Typowane operacje i opis procesu; plan sam nie udziela uprawnień |
| [Logs 0.5.0](https://github.com/wellmanifest/logs/tree/45b9672c3ae413f3b05af5c90986c478a899e3b3) | Zamknięta koperta audytu, SHA-256, łańcuch zdarzeń |
| [Data Plane](https://github.com/wellmanifest/data-plane/tree/fd62322a23c3ee2ae29c6ed878563b18d1316ccb) | Rozdzielenie CQRS, zdarzeń domenowych, projekcji i audytu |
| [SemVer](https://github.com/wellmanifest/semver/tree/432b6f94ef79c2cfbd84ba1ad697b46bbc5e4011) | Przyjęte wersjonowanie firmware/kontraktów; binarny schemat embedded-release pozostaje niezaliczony — GAP-SEMVER-FACTORY |

Kontrakt `data-plane.domain-event/v1` z upstream jest zamkniętym przykładem
licznika `counter.changed`. Nie zmieniamy go po cichu na zdarzenia Androida.
Gateway ma własny model domenowy i eksportuje oddzielny audyt Logs.

## URL, URI, URN i czas

| Warstwa | W projekcie | Własność i ograniczenia |
| --- | --- | --- |
| URL / SOA | Adres gateway oraz jawnie skonfigurowane endpointy `CLIK_MODEL_ENDPOINT`, `CLIK_STT_ENDPOINT`, `CLIK_TTS_ENDPOINT`, `CLIK_POA_ENDPOINT` | Parametry `.env`; brak kluczy w URL i katalogu operacji |
| URI / POA | `proc://clikvm/android/tap/v1`, `proc://clikvm/device/api/v1` i pozostałe URI z katalogu | Lokalne URI projektu; nie twierdzimy, że są już zarejestrowane w publicznym OQLLM |
| URN / dane | `urn:clikvm:frame:sha256:<digest>` oraz `urn:clikvm:state:sha256:<digest>` | Tożsamość treści; odczyt nadal wymaga uprawnienia do sesji/urządzenia |
| Czas zdarzeń | `occurredAt` UTC i ograniczony czas ważności sesji/akcji/lease | Znaczniki czasu nie zastępują kontroli sesji ani idempotencji |
| URT | Punkt integracji z opcjonalnym `uritime/urtlog` | Brak zweryfikowanej biblioteki w dostarczanym runtime; nie generujemy własnej składni pod nazwą URT |

Aktualny [profil URTLog w Logs](https://github.com/wellmanifest/logs/blob/45b9672c3ae413f3b05af5c90986c478a899e3b3/docs/information/urtlog-bridge-and-decision-slicing.md)
przewiduje `URT × URI × URN_IN × URN_OUT`, opt-in przez `URTLOG_STATE_DIR`,
leniwy import i awarię telemetrii nieprzerywającą głównej operacji. Publiczne
README/pyproject `uritime/urtlog` oraz rekord PyPI `uritime-urtlog` zwróciły
404 podczas przygotowania. Bridge nie jest deklarowany jako zaimplementowany.
Po udostępnieniu rzeczywistego API należy przypiąć wersję, dodać adapter po
commicie ES i test rzeczywistego `UrtLogStore`; pierwotny wynik wykonania
pozostaje niezależny od powodzenia telemetrii.

## CQRS + Event Sourcing i audyt

Polecenia zmieniają stan sesji, planów i lease. Zapytania odczytują stan.
Gateway zapisuje zdarzenie domenowe i kanoniczną kopertę audytu w tej samej
transakcji SQLite. Pliki JSONL są eksportem po commicie, więc awaria eksportu
nie może unieważnić już utrwalonego wykonania. Stan eksportu wymaga obserwacji
i ponowienia; nie wolno ponawiać kliknięcia tylko dlatego, że log nie został
zapisany do pliku.

Zmiany pięciu tabel projekcji przechwytują triggery SQL. Zdarzenia
`projection_changed` zawierają pełne wiersze potrzebne do replay, a
`clikvm rebuild-projections --destination NOWY_KATALOG` odtwarza ich stan
z tabeli zdarzeń do nowej bazy i kopiuje zachowane PNG oraz JSON stanu.
Replay jawnie uzupełnia brakujące `sessions.profile` i `frames.kind`
w wierszach 0.1.0; testy migracji obejmują również starą postać tabel. Oryginał pozostaje
nienadpisany; podczas operacji gateway musi być zatrzymany. Prywatny dziennik
domenowy może zawierać tekst celu i polecenia. Ograniczenia dotyczące
`rawOutputIncluded=false` odnoszą się do oddzielnej koperty Logs, która ma
wyłącznie hash wejścia. Cache TTS nie jest częścią odtwarzanych projekcji.

`gateway/clikvm/audit.py` tworzy kopertę z polami upstream. `eventHash` to
SHA-256 kanonicznego JSON bez pola `eventHash`. Pierwszy `previousHash` ma
64 zera. Kolejne zdarzenia wiążą poprzedni hash i ciągłą sekwencję. Pola
`rawOutputIncluded` i `secretMaterialIncluded` pozostają `false`; payload
wejściowy występuje wyłącznie jako hash. Kod błędu jest obecnie `null`.

Kanoniczne zdarzenie nie zawiera surowego screena, audio, promptu ani tokenu.
Hash nie jest sam w sobie anonimizacją danych o małej entropii. Uprawnienia
do treści obrazów oraz retencja są odrębnymi mechanizmami gateway.

## Plan, uprawnienie i wykonanie

LLM proponuje `Plan` z walidowanym wariantem `Action`. Nie zwraca dowolnego
shella. Plan odnosi się do konkretnej klatki i sesji. Gateway wydaje
ograniczony czasowo lease; ESP32 rozpoznaje identyfikator wykonania.
Niepewny wynik jest stanem wymagającym rozstrzygnięcia, a nie zgodą na
automatyczne ponowienie operacji. Nowa sesja i nowa obserwacja ograniczają
użycie nieaktualnych poleceń.

To implementacja granicy wykonania w clikVM. Nie deklarujemy, że lokalny
lease jest niezależnie wydanym grantem floty `wellmanifest/authority-lifecycle`
ani że statyczny plik profilu nadaje uprawnienia.

## Rozszerzenie 0.2.0: zakres możliwości i API

`TargetProfile` jest deklaracją adaptera sprawdzaną przez gateway, a nie grantem
floty. Connector najpierw sprawdza własne interfejsy i przesyła obserwację.
Operacja modelu musi pasować do `action_types`, do listy klawiszy oraz —
w przypadku API — do zarejestrowanego ID i zamkniętego JSON Schema argumentów.
Profil zawiera opis i schemat, lecz nie URL, nagłówki ani sekrety usługi.
`requires_confirmation` z lokalnego katalogu jest obowiązkowe nawet wtedy,
gdy model proponuje brak potwierdzenia.

Eksport POA zachowuje oficjalny zamknięty schemat. Pydantic definiuje typ
`JsonValue` poprzez lokalne `$defs`; generator rozwiązuje lokalne odwołania
inline bez zmiany znaczenia walidacji. Rekurencyjne i zewnętrzne odwołania
zatrzymują eksport. Nie dodano niedozwolonego `$defs` do top-level
`input_schema` ani nie rozszerzono skopiowanego kontraktu. Limity rozmiaru,
profil docelowy i konkretny katalog argumentów pozostają kontrolami runtime;
sam rekord `device.api` nie daje dostępu do dowolnego endpointu.

Kontroler M5 ma odrębnego aktora i token ograniczony do jednego targetu.
Nie może potwierdzać akcji operatora, wydawać sobie lease ani przesyłać
fałszywej obserwacji. `expected_session_id` wiąże cel głosowy z sesją widzianą
przed nagraniem, a nowy obraz/stan jest wymagany przed planowaniem.
`json-state` zachowuje własny URN i typ mediów; sam kanał KM bez obrazu/stanu
nie może uruchomić autonomicznego celu. Szczegóły kontraktu:
[gateway/docs/UNIVERSAL.md](../gateway/docs/UNIVERSAL.md) oraz
[CONTROLLERS.md](../gateway/docs/CONTROLLERS.md).

## Luka narzędzia i zakres formalnej adopcji

Raport `wellman check` jest dostarczany w `docs/analysis/wellman-check.json`.
Końcowy odczyt 2026-11-21, po dodaniu indeksu dokumentacji i AGENTS, zwrócił
**exit 1, `valid: false`, 9 błędów i 1 ostrzeżenie**. Nie zamieniamy tego
wyniku na komunikat pełnej zgodności. `validation_scope` narzędzia to
`implemented-checks`; `runtime_enforcement` ma wartość `not_checked`.

| Ustalenie checkera | Liczba | Interpretacja |
|---|---:|---|
| `GOV-POA-INVALID` + `GOV-POA-STEP` | 7 błędów | Konflikt oczekiwań checkera z przypiętym zamkniętym schematem procesu |
| `GOV-LLM-MISSING` | 1 błąd | Brak deklaracji nieużywanego profilu Subactor-first/MCP |
| `GOV-AUTHORITY-LIFECYCLE-MISSING` | 1 błąd | Niewdrożona niezależna authority lifecycle floty |
| `GOV-LOCK-MISSING` | 1 ostrzeżenie | Brak locka pełnej zarządzanej adopcji new-project; nasz source lock jest innym formatem |

- **GAP-POA-CHECKER:** przypięty Wellman wymaga `process_id` i
  `steps[].action/operation`. Kanoniczny zamknięty schemat POA wymaga
  `process_ref` i `steps[].capability_ref`, a dodatkowych pól zabrania.
  `.governance/poa.json` zachowuje kanoniczny kontrakt. Offline walidacja
  przechodzi, a checker Wellmana zgłasza tę niezgodność adaptera.
  Dowody: [check_poa](https://github.com/wellmanifest/wellman/blob/e8946f97c5a28e4162c4a5a91e19e2bab2713925/src/wellman/catalog_pack_checkers.py)
  i [poa-process.schema.v1.json](https://github.com/wellmanifest/poa/blob/cba52778fcc2d942fe879bc71b13695368c38647/standard/poa-process.schema.v1.json).
- **GAP-LLM-PROFILE:** obecny upstream `llm-profile.schema.json` wymusza
  profil Subactor-first i transport MCP. Nasza aplikacja używa OQLLM przez
  HTTPS. Rzeczywista granica jest opisana w
  `.governance/llm-boundary.json` jako format projektu. Nie deklarujemy
  zainstalowanego Subactora, MCP ani pełnej zgodności z tamtym profilem.
  Dowód: [przypięty schemat profilu LLM](https://github.com/wellmanifest/llm/blob/25b3c1399112529862830e4d4ce4dedecf426c30/models/llm-profile.schema.json).
- **GAP-FLEET-AUTHORITY:** pełna adopcja niezależnych grantów, chronionej
  walidacji, delegacji i publikacji wymaga infrastruktury operatora.
  Rejestracja wymagań S4/S5 nie stanowi dowodu jej działania.
- **GAP-URT-RUNTIME:** brak zweryfikowanego publicznego pakietu URTLog;
  rozszerzenie pozostaje opcjonalne i nie blokuje wykonania ADB.
- **GAP-SEMVER-FACTORY:** przypięty
  [embedded-release.schema.json](https://github.com/wellmanifest/semver/blob/432b6f94ef79c2cfbd84ba1ad697b46bbc5e4011/schemas/embedded-release.schema.json)
  wymaga dla komponentu mikrokontrolera pola
  `binary_identity.target_partition` o wartości `ota_0`, `ota_1` albo
  `app_dual`. Rzeczywisty build używa partycji **`factory`** pod offsetem
  `0x20000`, zgodnie z `firmware/partitions.csv`; w tym wydaniu nie ma OTA.
  Nie zmieniamy skopiowanego schematu ani nie wpisujemy fikcyjnej partycji.
  Wersjonowanie SemVer jest przyjęte, lecz **nie deklarujemy zaliczenia
  binarnego schematu Wellmanifest embedded-release**. Pakiet wydania ma
  jawnie własny format `clikvm.release/v1`, opisujący rzeczywiste SHA-256,
  offsety obrazów, commit i niepotwierdzony sprzętowo status HIL (`false`).
  Ten manifest nie jest certyfikatem Wellmanifest. Ograniczenie ustalono
  przez porównanie kontraktu z buildem; nie jest dodatkowym błędem
  wykazanym w powyższym raporcie `wellman check`.

Zakres dobranego profilu i odrzucenie zbyt szerokiego `agent-executor`
opisuje [ADR-001](analysis/ADR-001-runtime-service.md).

## Powtarzalne polecenia

Po instalacji zależności projektu i `jsonschema==4.26.0`:

```bash
python scripts/standards.py check
python -m unittest discover -s gateway/tests -p test_audit.py -v
python scripts/standards.py check --events .state/gateway/logs/wellmanifest-events.jsonl
```

Po zmianie modeli `Action` wygeneruj nowy eksport i obejrzyj diff:

```bash
python scripts/standards.py generate
python scripts/standards.py check
```

Osobne środowisko rzeczywistego Wellmana:

```bash
python3 -m venv .venv-governance
.venv-governance/bin/pip install -r .governance/requirements.txt
.venv-governance/bin/wellman --version
.venv-governance/bin/wellman check --root . --json
```

Sprawdzenie źródeł w sieci jest odrębną, jawną operacją tylko do odczytu:

```bash
python scripts/standards_upstream.py
```

Skrypt niczego nie aktualizuje. Weryfikuje hashe przypiętych plików. Zmiana
standardu wymaga nowego pinu, obejrzenia różnic i ponownych kontroli. Nie
kopiuj z innych repozytoriów cudzych lease, approval receipts ani kluczy.
