---
{
  "schema": "wellmanifest.docs/document/v1", "id": "clikvm-release-validation",
  "kind": "information", "version": 2, "title": "Dowody walidacji wydania 0.2.0",
  "status": "draft", "owner": "clikVM/m5stack", "scope": "repository",
  "created": "2026-11-21", "updated": "2026-11-21", "review_after": "2026-12-21",
  "evidence": ["analysis/firmware-builds.json", "analysis/software-tests.log", "../gateway/docs/verification-report.json", "../agents/desktop/verification-report.json"]
}
---

# Walidacja 0.2.0 — 21 listopada 2026

**Wykonano rzeczywiste kompilacje ESP-IDF, testy programowe i lokalne integracje
TCP. Nie podłączono fizycznego CoreS3, Waveshare, telefonu ani pulpitu Windows,
macOS lub X11. Nie użyto prywatnych produkcyjnych endpointów OQLLM.**
Wydanie jest przygotowane do kwalifikacji konkretnego zestawu.

## Firmware: pełna macierz dziewięciu profili

ESP-IDF **5.4.2**, rzeczywisty toolchain Xtensa i przypięte managed components.
Nowy HID korzysta z esp_tinyusb **1.7.6**, a dokładne wersje i hashe są
w `firmware/dependencies.lock`. Wszystkie obrazy zgłaszają wersję **0.2.0**.

| Profil | Tryb | Rozmiar aplikacji | Wynik / ostrzeżenia |
|---|---|---:|---|
| `cores3` | android_usb_host | 1 171 600 B | PASS / 0 |
| `lcd154` | android_usb_host | 1 171 200 B | PASS / 0 |
| `lcd154-touch` | android_usb_host | 1 171 376 B | PASS / 0 |
| `cores3-network` | network_controller | 1 108 112 B | PASS / 0 |
| `lcd154-network` | network_controller | 1 108 240 B | PASS / 0 |
| `lcd154-touch-network` | network_controller | 1 108 416 B | PASS / 0 |
| `cores3-hid` | usb_hid_device | 1 135 664 B | PASS / 0 |
| `lcd154-hid` | usb_hid_device | 1 135 376 B | PASS / 0 |
| `lcd154-touch-hid` | usb_hid_device | 1 135 568 B | PASS / 0 |

Dziesiąty build sprawdził dodatkowe gałęzie **HID + TTS + self-powered**:
PASS, 0 ostrzeżeń. Ma celowo nieustawiony monitor VBUS (`GPIO -1`), przez co
blokuje start USB. To dowód kompilacji gałęzi, a nie obraz użytkowy; nie jest
pakowany do katalogu `binaries/` wydania.

W standardowych dziewięciu obrazach TTS jest wyłączone, a gateway wymaga HTTPS.
Wariant HID jest domyślnie bus-powered. Testy elektryczne oraz podłączenie USB
pozostają NOT_RUN. App flash offset to **0x20000**, bootloader **0x0**, partycje
**0x8000**; używaj `flash_args` danego profilu.

[firmware-builds.json](analysis/firmware-builds.json) zawiera hashe binariów,
44 plików źródłowych firmware i mapę profili do logów. Źródła oraz binaria
ponownie porównano z dowodami przed pakowaniem.

## Testy gatewaya, wykonawców i protokołów

Ostatni zbiorczy `make test` po `make setup`: **exit 0**. Pełny zapis:
[software-tests.log](analysis/software-tests.log).

| Kontrola | Wynik | Zakres |
|---|---|---|
| Gateway | **46/46 PASS** | Android 0.1, profile, kontrolery, JSON/API, sesje, leases, idempotencja, autoryzacja, restart i replay |
| Audyt Logs | **4/4 PASS** | Kanoniczny schemat, hashchain, mutacje i niepoprawne wejścia |
| Desktop/RFB/API | **27/27 PASS** | Wykonawcy, journal, świeżość, realny TCP, utrata ACK i awarie wejścia |
| Natywny protokół C | **6 grup PASS** | 490 fragmentowanych roundtripów, exact MPS, checksum i 50 000 uszkodzonych wejść |
| Shell quoting | **6/6 PASS** | Rzeczywisty `/bin/sh`, zachowanie argumentów, brak wykonania wstrzykniętych komend |
| Kanoniczne standardy | **5/5 PASS** | 15 rekordów POA, 8 logicznych akcji, 2 procesy, piny i Logs |
| Pakowanie Python | **2 wheel PASS** | Gateway i clikvm-node z metadanymi 0.2.0 |

Testy desktopowe obejmują trzy kompletne pętle z właściwym Uvicorn gatewayem
oraz kontrolowanym serwerem modelowym. RFB i API używają rzeczywistych socketów
TCP i HTTP. Native wykonuje prawdziwą ścieżkę kodu, lecz granica API pulpitu
jest jawną atrapą. To nie jest fizyczna walidacja danego OS.

Osobno wykonano prawdziwe CLI `doctor`: RFB i HTTP API na lokalnych serwerach
kontrolnych oraz prawidłową odmowę native bez X11 DISPLAY. Dowody:
[raport node](../agents/desktop/verification-report.json),
[capability probes](../agents/desktop/verification/capability-probes.json).

### Błędy wykryte i zamknięte podczas przeglądu

- **X11, wiele monitorów:** mismatch rozmiaru X root i przechwytywanego monitora
  blokuje rejestrację, obserwację lub wejście; nie skaluje kliknięcia na drugi ekran.
- **RFB, częściowe wysłanie press:** cleanup obejmuje także raport, którego wynik
  wysłania jest niepewny. Podejmowane są próby wszystkich release, a niepewny
  transport jest zamykany. Testy obejmują błąd pierwszego press i późniejszego release.
- **Zmieniona sesja podczas głosu:** expected_session_id powstrzymuje wykonanie
  celu na nowej sesji. Oddzielny token kontrolera nie może przyznać sobie lease.
- **PNG/JSON w panelu:** spóźniona odpowiedź poprzedniego urządzenia nie zastępuje
  aktualnego widoku; stan JSON nie jest przedstawiany jako sztuczny screenshot.

Szczegółowy [raport gatewaya](../gateway/docs/verification-report.json) oraz
raport node wiążą wyniki z hashami źródeł. ASan i UBSan były aktywne w testach C;
LeakSanitizer wyłączono z powodu środowiska ptrace. Ostrzeżenie testowego
Starlette/httpx nie powodowało pomijania testów.

## Panel operatora

**7/7 bramek DOM + live HTTP PASS**. Dowody: [frontend-tests.log](analysis/frontend-tests.log)
i [browser-contract-check.json](analysis/browser-contract-check.json). Końcowa
kontrola wykryła wyścig testu capture: handler uruchamiał fetch bez zwracania Promise.
Handlery capture/cancel zwracają teraz łańcuch Promise, a test celowo zatrzymuje
POST i sprawdza flagę przed oraz po jego zakończeniu, bez sztucznego sleep.
Po poprawce ponownie przeszło 46 testów gatewaya i 7 bramek DOM/HTTP.
Ten harness sprawdza rzeczywisty JavaScript i HTTP, nie wygląd renderowanej przeglądarki. Osobna strona SET ma własne kontrole wizualne;
nie są one dowodem działania sprzętu ani panelu gatewaya.

## Rzeczywisty Wellman

Rzeczywisty Wellman **0.23.2**, z przypiętej rewizji upstream:
`wellman check --root . --json` daje **exit 1, valid=false, 9 błędów i 1 ostrzeżenie**.
Raport: [wellman-check.json](analysis/wellman-check.json).

- 7 błędów: checker POA oczekuje pól innych niż zamknięty kanoniczny schemat.
- 1 błąd: brak wymaganego upstream profilu LLM Subactor/MCP; integracja projektu
  faktycznie używa skonfigurowanych usług OQLLM/HTTP.
- 1 błąd: brak infrastruktury authority-lifecycle niezależnych grantów floty.
- 1 ostrzeżenie: brak zarządzanego manifest.lock.

Dodatkowo pozostają jawne luki URT runtime oraz embedded-release SemVer
wymagający partycji OTA zamiast używanej factory. Nie zmieniono upstream
schematów dla uzyskania zielonego wyniku. Lokalna walidacja nie jest certyfikatem
pełnej adopcji floty. Szczegóły: [standards.md](standards.md).

## Niewykonane kontrole

| Obszar | Status | Potrzebny dowód |
|---|---|---|
| VBUS, CC, zasilanie, LCD/audio i USB host/device | NOT_RUN | Prawdziwa płytka, rewizja, przewód, pomiary i scenariusze HIL |
| Android AOA/RSA/ADB i OEM | NOT_RUN | Własny telefon, autoryzacja i test działań z obrazem |
| Native Windows/macOS/X11 | NOT_RUN | Rzeczywista sesja, zgody, skalowanie i geometria ekranu |
| Konkretny serwer VNC z TLS/tunelem | NOT_RUN | Produkt/wersja, autoryzacja, obraz i wejście |
| Produkcyjne HTTP API/Home Assistant | NOT_RUN | Własny kontrakt, encja i weryfikacja efektu |
| OQLLM model, STT, TTS | NOT_RUN | Rzeczywiste endpointy, uprawnienia i jakość odpowiedzi |
| Docker/Caddy runtime | NOT_RUN | Start obrazów i HTTPS w środowisku z Dockerem |
| GitHub push, Actions, branch protection | NOT_RUN | Uwierzytelniony dostęp do docelowych repozytoriów |

Pełne kompilacje oraz testy lokalnego TCP nie wypełniają żadnego z tych wierszy.
Kryteria są w [hardware-validation.md](hardware-validation.md),
[controller-modes.md](controller-modes.md) i [desktop-agent.md](desktop-agent.md).

## Wydanie i historia

Paczka obejmuje źródła, dziewięć obrazów, bootloadery, partycje, flash_args,
release-manifest.json, SHA256SUMS i lokalny repository.bundle. Manifest
`clikvm.release/v1` podaje commit, wersje, profile, hashe oraz
`hardware_validated:false`. Skrypt pakujący odrzuca rozbieżność źródeł/binariów
z dowodami i przypadkowo śledzone sekrety. ZIP sprawdzany jest przez CRC.

Raporty 0.1 zachowano w `analysis/v0.1/`; identyfikują historyczny stan i nie
są przedstawiane jako wynik kodu 0.2. Poprzednia paczka pozostaje odrębna.

### Ponowna próba na CoreS3 K128 po publikacji 0.2.0

Na fizycznym CoreS3 K128 obraz `cores3` z oryginalnego ZIP-u 0.2.0
przeszedł weryfikację po zapisie, lecz restartował się przy inicjalizacji
Octal PSRAM z identyfikatorem `0x00000000`. Błąd wystąpił także z bootloaderem
ESP-IDF 5.4.2 z wydania. Działający wcześniej profil tej płytki używał Quad
PSRAM. Profile CoreS3 wybierają teraz Quad. Kryterium próby na sprzęcie
obejmuje wykrycie pamięci i uruchomienie aplikacji po resecie.
