---
{
  "schema": "wellmanifest.docs/document/v1", "id": "clikvm-deployment",
  "kind": "instruction", "version": 1, "title": "Wdrożenie i obsługa gatewaya",
  "status": "draft", "owner": "clikVM/m5stack", "scope": "repository",
  "created": "2026-11-21", "updated": "2026-11-21", "review_after": "2026-12-21"
}
---

# Wdrożenie gatewaya

## Model działania

Jeden proces `clikvm serve` jest właścicielem jednego katalogu stanu. Gateway
można uruchomić obok OQLLM i wystawić pod osobną domeną HTTPS. Nie wymaga USB
ani dostępu do telefonu po stronie serwera. ESP32 realizuje USB lokalnie.

`CLIK_MODEL_ENDPOINT` i endpointy audio są osobnymi integracjami. Konfiguracja
serwera nie udostępnia kluczy modelu płytce. Profil Android host ma token
urządzenia, a network/HID ma odrębny token kontrolera przypisany do celu.
Panel operatora ma własny token o szerszych uprawnieniach. Kontrakt i generator
opisuje [CONTROLLERS.md](../gateway/docs/CONTROLLERS.md).

## Docker Compose

Przygotuj `.env` przez generator lokalnego CLI, wpisz faktyczne endpointy,
ustaw `CLIK_ALLOW_SIMULATION=0`. Następnie:

```sh
docker compose config --quiet
docker compose up --build -d gateway
docker compose ps
```

Port 8787 jest przypięty tylko do loopback hosta. Kontener zapisuje stan do
wolumenu `gateway-state`, działa jako UID/GID 10001 i ma system plików aplikacji
tylko do odczytu. Nie używaj kilku replik lub `uvicorn --workers N` z tym samym
SQLite. Drugi właściciel katalogu stanu zostanie odrzucony.

Dodany Dockerfile i Compose nie zostały uruchomione w środowisku przygotowania
wydania, w którym Docker był niedostępny. CI ma oddzielny test obrazu; jego
wynik na GitHub powstanie dopiero po opublikowaniu repozytorium.

## HTTPS z Caddy

W `.env` ustaw własne `CLIK_PUBLIC_HOST` i `CLIK_ACME_EMAIL`. Domena musi kierować
na serwer, a porty 80/443 muszą być osiągalne dla wydania certyfikatu.
Nie zostawiaj przykładowej domeny `.example.com`.

```sh
docker compose --profile https up --build -d
```

`deploy/Caddyfile` przekazuje ruch do `gateway:8787`. Jeśli masz już reverse proxy,
użyj własnej konfiguracji i samej usługi gateway. Zachowaj Authorization,
Idempotency-Key, X-Frame-Id i X-Clik-Error-Code; umożliwiaj żądania chunked
oraz obrazy/audio do skonfigurowanego limitu. Timeout proxy powinien obejmować
czas dostawcy STT/TTS, domyślnie 45 s plus narzut sieci.

Nie używaj wyłączenia weryfikacji certyfikatu ESP32 jako sposobu wdrożenia.
Domyślnie firmware używa bundle CA z ESP-IDF i czasu SNTP. Własny prywatny CA
wymaga świadomej zmiany firmware/bundle oraz nowej kwalifikacji połączenia.
Źródło konfiguracji reverse proxy: [Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

## Utrzymanie i odzyskiwanie

`GET /health` jest lekką kontrolą dostępności; nie testuje prawdziwego modelu,
USB ani jakości PNG. Obserwuj `event_export`, wielkość katalogu stanu,
liczbę niepewnych akcji oraz błędy dostawców. Nie włączaj pełnych logów
nagłówków Authorization ani treści obrazów w reverse proxy.

Wstrzymana akcja może zostać wykonana na telefonie mimo utraconej odpowiedzi.
Nie rozwiązuj tego przez automatyczne ponowne kliknięcie. W panelu pobierz
nowy obraz, sprawdź wynik i zakończ niepewną próbę. Nowa sesja po wygaśnięciu
lub zmianie USB nie wznawia starego celu.

Przy odroczonym eksporcie JSONL zatrzymaj gateway i wykonaj:

```sh
.venv/bin/clikvm --env-file .env export-events
.venv/bin/python scripts/standards.py check \
  --events .state/gateway/logs/wellmanifest-events.jsonl
```

W kontenerze użyj jednorazowego procesu dopiero po zatrzymaniu głównego:

```sh
docker compose stop gateway
docker compose run --rm gateway clikvm export-events
docker compose up -d gateway
```

Odtwarzanie projekcji w instalacji lokalnej:

```sh
.venv/bin/clikvm --env-file .env rebuild-projections \
  --destination .state/rebuilt-review
```

Docelowy katalog musi być nowy. Polecenie odtwarza pięć tabel ze zdarzeń,
kopiuje zachowane obrazy, nie nadpisuje oryginału i nie przywraca mediów
już usuniętych przez retencję. Cache TTS jest odtwarzalnym cache, nie projekcją.
Sprawdź wynik przed świadomą zmianą `CLIK_STATE_DIR`.

## Backup i retencja

Najprostsza spójna kopia wymaga zatrzymania jedynego procesu gatewaya oraz
skopiowania całego katalogu stanu lub wolumenu, wraz z SQLite i PNG.
Oddzielnie przechowuj `.env` w swoim magazynie sekretów. Przywracaj do nowego
katalogu i sprawdzaj audyt przed użyciem. Sam `wellmanifest-events.jsonl`
nie zawiera całych treści domenowych i nie zastępuje kopii bazy.

Limit 256 MiB dotyczy prywatnych mediów; utrzymywane jest domyślnie 8 klatek
na sesję. **Nie ogranicza on wzrostu kanonicznego dziennika SQLite i eksportów.**
Wersja 0.2 nie ma automatycznej archiwizacji/przycinania event store. Monitoruj
dysk i ustal okres przechowywania, zanim obsłużysz wiele urządzeń przez długi czas.

Tokeny i dane Wi-Fi w standardowym buildzie NVS nie są szyfrowane at rest.
Środowisko fizyczne i kopie bazy należy chronić. Secure Boot, flash encryption,
rotacja kluczy floty, konta wielu operatorów i zarządzanie sekretami są pracą
wdrożeniową, nie zadeklarowaną cechą tej wersji.

## Publikacja repozytorium

Projekt został przygotowany lokalnie z origin
`https://github.com/clikVM/m5stack.git`. Brak uwierzytelnienia i odpowiedź 404
bez niego nie pozwoliły zweryfikować ani wykonać push do docelowego repozytorium.
W środowisku z właściwym dostępem sprawdź remote i wyślij gałąź:

```sh
git remote -v
git push -u origin main
```

Nie używaj force push do zastępowania istniejącej historii. Jeśli repozytorium
ma już commity, najpierw pobierz je i połącz projekt zgodnie z jego regułami.
Paczka może zawierać `repository.bundle`: `git clone repository.bundle m5stack`
odtwarza dokładny lokalny commit, następnie ustaw origin na docelowy URL.

Workflow CI jest dostarczony, ale nie został uruchomiony na GitHub bez push.
Nie ustawiono zdalnych wymaganych checks ani ochrony gałęzi; te działania
wymagają dostępu administracyjnego i rzeczywistej konfiguracji repozytorium.
