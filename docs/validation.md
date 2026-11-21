---
{
  "schema": "wellmanifest.docs/document/v1", "id": "clikvm-release-validation",
  "kind": "information", "version": 1, "title": "Dowody walidacji wydania 0.1.0",
  "status": "draft", "owner": "clikVM/m5stack", "scope": "repository",
  "created": "2026-11-21", "updated": "2026-11-21", "review_after": "2026-12-21",
  "evidence": ["analysis/firmware-builds.json", "analysis/software-tests.log", "../gateway/docs/verification-report.json"]
}
---

# Walidacja 0.1.0 — 21 listopada 2026

**Wykonano rzeczywiste kompilacje i testy programowe. Nie podłączono fizycznego
CoreS3, Waveshare ani telefonu. Nie uruchomiono zapytań na prywatnych usługach
modelowych użytkownika.** Wydanie jest gotowym punktem startowym kwalifikacji
sprzętowej, nie certyfikatem produktu działającego z dowolnym Androidem.

## Pełne buildy ESP32-S3

Użyto pobranego ESP-IDF **v5.4.2**, toolchainu Xtensa Espressif i rzeczywistych
zależności z `firmware/dependencies.lock`. Kompilacja obejmuje `main`, board,
cloud, USB, RSA i protokół, a nie sam parser lub atrapy nagłówków.

| Profil | Wynik | Aplikacja | Ostrzeżenia kompilatora |
|---|---|---:|---:|
| CoreS3 / external BUS | PASS | 1 170 000 B | 0 |
| Waveshare LCD-1.54 | PASS | 1 169 600 B | 0 |
| Waveshare Touch-LCD-1.54 | PASS | 1 169 776 B | 0 |
| Dodatkowy CoreS3: battery + TTS + HTTP LAN | PASS | 1 171 792 B | 0 |

Wariant dodatkowy sprawdza kompilację alternatywnych gałęzi. Standardowa paczka
zawiera trzy podstawowe obrazy, z TTS oraz HTTP LAN domyślnie wyłączonymi.
Piny i elektryczne działanie wariantu battery nadal wymagają własnego HIL.

[firmware-builds.json](analysis/firmware-builds.json) zawiera hashe binariów,
hash każdego pliku źródłowego firmware i ścieżki zachowanych logów kompilacji.
Bootloader trafia pod `0x0`, partycje pod `0x8000`, aplikacja pod **`0x20000`**.
Nie wykonano flashowania ani sprawdzenia czasu rzeczywistego na MCU.

## Gateway i protokół

Końcowe `make test` wykonane w głównym środowisku zakończyło się exit 0.
Pełny output: [software-tests.log](analysis/software-tests.log).

| Kontrola | Wynik i zakres |
|---|---|
| API/gateway | **30/30 PASS**: autoryzacja, obrazy, planner, akcje, retry, lease, restart, replay, retencja, STT/TTS i panel |
| Audyt Wellmanifest | **4/4 PASS**: oficjalny schemat, hashe, mutacje i niepoprawne wejścia |
| Natywny protokół C | **6 grup PASS**, 490 podziałów/roundtripów pakietów, dokładne MPS, 50 000 uszkodzonych wejść |
| Shell quoting | **6 rzeczywistych przypadków `/bin/sh` PASS**; zachowanie argumentów, bez wykonania wstrzykniętego polecenia |
| Symulator urządzenia | PASS na prawdziwym lokalnym TCP/HTTP; syntetyczne PNG i skryptowy HOME |
| Panel: DOM + HTTP | **7/7 PASS** z jsdom, w tym wyścig zmiany urządzenia i chronione obrazy |
| Kanoniczne kontrakty | **5/5 PASS**: piny, rejestracja, POA operacje/proces i koperta Logs |
| Przypięte źródła upstream | **9/9 SHA-256 PASS** w odrębnej kontroli online |

AddressSanitizer i UndefinedBehaviorSanitizer były aktywne w testach C.
Wyłączono wyłącznie LeakSanitizer, ponieważ nie działa pod ptrace w tym
środowisku. Testy gatewaya pokazały ostrzeżenie deprecacji testowego adaptera
Starlette/httpx; żaden test nie został z tego powodu pominięty.

Zbudowano również rzeczywisty pakiet Python wheel i sprawdzono obecność CLI,
API oraz wszystkich trzech plików panelu. Przypięte wersje zależności z tego
środowiska znajdują się w `requirements.lock`. Skrypt `scripts/firmware.sh`
sprawdzono na pełnym profilu CoreS3; zachował hash zweryfikowanego obrazu.

Raport [gateway verification](../gateway/docs/verification-report.json)
wiąże wyniki z hashami plików gatewaya. Testy dostawców używają kontrolowanego
transportu HTTP: sprawdzają format wysyłanego obrazu/audio i interpretację
odpowiedzi, ale nie jakość modelu ani dostępność płatnego API.

DOM harness jest odtwarzalny w `tests/frontend`. Nie sprawdza wyglądu przeglądarki.
Osobna próba Chromium została zablokowana przez środowisko przy tworzeniu socketu;
**rendered browser NOT_RUN**. Nie przedstawiamy wyników DOM jako screenshotu
gotowej strony ani testu dotyku na LCD.

## Rzeczywisty Wellman

Uruchomiono Wellman **0.23.2**, przypięty do źródłowego commita.
`wellman check --root . --json` zwraca **exit 1 / valid=false: 9 błędów,
1 ostrzeżenie**. Wyniku tego nie zamieniono na PASS.

- 7 błędów: checker procesu POA oczekuje innych pól niż zamknięty kanoniczny schemat.
- 1 błąd: profil LLM wymaga Subactor/MCP, a rzeczywista integracja to OQLLM/HTTPS.
- 1 błąd: brak infrastruktury niezależnych grantów i uprawnień floty.
- 1 ostrzeżenie: brak pełnego zarządzanego `manifest.lock.json`.

Osobno zidentyfikowano niepasujący do partycji factory binarny schemat SemVer
oraz brak zweryfikowanego runtime URTLog. Lokalne hashe i katalog operacji nie
są przedstawiane jako rozwiązanie tych luk. [standards.md](standards.md)
zawiera przyczyny, dokładne kontrakty i przypięte źródła.

## Pozostałe niewykonane kontrole

| Obszar | Status | Co go zamknie |
|---|---|---|
| VBUS, CC, zasilanie CoreS3/Waveshare | NOT_RUN | Pomiary rzeczywistego toru i stabilność pod obciążeniem |
| AOA, RSA, ADB i Android OEM | NOT_RUN | Właściwy telefon, kabel i świadoma autoryzacja |
| LCD, dotyk, mikrofony i głośnik | NOT_RUN | Próby fizycznej rewizji urządzenia |
| Model, STT, TTS na prywatnym OQLLM | NOT_RUN | Rzeczywiste endpointy i własne klucze dostępu |
| Docker/Caddy | NOT_RUN lokalnie | Uruchomienie Compose lub pierwszego workflow CI |
| GitHub push, Actions i ochrona gałęzi | NOT_RUN | Uwierzytelniony dostęp do `clikVM/m5stack` |

Repozytorium Git przygotowano lokalnie. `gh auth status` nie wykazuje logowania,
a publiczny odczyt docelowego repo zwracał 404. Nie twierdzimy, że projekt został
opublikowany lub że wymagane checks ustawiono na GitHub.

## Zasada wydania

Paczka zawiera sources, trzy obrazy, bootloadery, partycje, `flash_args`,
`release-manifest.json`, `SHA256SUMS` i opcjonalny lokalny `repository.bundle`.
Manifest ma jawny format projektu `clikvm.release/v1`, commit, wersję, rozmiary,
hashe i `hardware_validated:false`. Nie jest podpisanym poświadczeniem floty.
Skrypt odrzuca zmienione źródła firmware, inny obraz niż zweryfikowany oraz
przypadkowo śledzone sekrety. ZIP jest ponownie odczytywany i sprawdzany CRC.

Przed pokazem klientowi przejdź wszystkie H01–H10 w
[hardware-validation.md](hardware-validation.md) na jednym konkretnym zestawie.
Zapisz dowody i dopiero wtedy zmieniaj statusy danego zestawu na PASS.
