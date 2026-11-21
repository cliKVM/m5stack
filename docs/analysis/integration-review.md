# Przegląd integracji — 2026-11-21

Zakres: odczyt rzeczywistego CCloud, modeli gatewaya i JavaScriptu paneli;
test DOM z prawdziwym serwerem HTTP. Nie wykonywano połączenia USB z telefonem.

## Sprawdzone kontrakty

| Granica | Wynik / zakres |
|---|---|
| Modele siedmiu akcji i parser CCloud | Zgodne nazwy i pola: tap, swipe, key, text, open_url, screenshot, wait |
| Liczby i tekst | Gateway dopuszcza węższy, zgodny zakres: współrzędne 0..16383; swipe 50..3000 ms; wait 50..5000 ms; tekst wejścia ASCII bez `%`; URL do 1024 znaków |
| Klawisze | Zgodne HOME, BACK, ENTER, TAB, ESCAPE, DPAD kierunki, DEL i APP_SWITCH |
| Wynik akcji | `lease_id`, status succeeded/failed/uncertain, ograniczony detail |
| Idempotencja | UUID panelu ma 36 znaków; firmware ma 32 znaki hex; rzeczywisty klucz `result:<UUID36>:<UUID36>` ma 80 znaków — mieści zakres 8..128 backendu |
| Własność obrazu | PNG wymaga autoryzacji; panel pobiera binary z Bearer i tworzy URL blob; token urządzenia nie może wylistować wszystkich sesji operatora |
| Dane w DOM | Status, powód, polecenie i błędy trafiają przez `textContent`; obraz jest PNG, nie HTML; token nie jest zapisywany do localStorage/sessionStorage |
| Powrót po utracie odpowiedzi | Powtórzenie wyniku ma stały klucz. Konflikt lease wymaga obserwacji i uzgodnienia; nie uprawnia do ponownego kliknięcia |
| Pause i wynik już wykonanego wejścia | Aktualny `main.c` wysyła oczekujący wynik przed sprawdzeniem `paused`; pause blokuje kolejne wejście, nie blokuje potwierdzenia poprzedniego kroku |
| Opcjonalny TTS | Aktualny `main.c` odtwarza komunikat raz przy stanie `done`; nie czeka na TTS pomiędzy wykonaniem akcji a POST jej wyniku |
| Termin dzierżawy | CCloud zapisuje zegar sprzed POST `actions/next`; `execute` odejmuje cały upływ, w tym RTT, od `valid_for_ms`; wait nie zaczyna się, jeśli jego czas przekracza resztę lease |
| Wygaśnięcie sesji / konflikt wyniku | Nagłówek `X-Clik-Error-Code` rozróżnia session_expired/session_not_found; nowa sesja nie ponawia starego celu. Wynik 409 kończy retry i dopuszcza nowy obraz do uzgodnienia |

Wykryte w przeglądzie błędy przekazano właścicielom komponentów: rozbieżny
alfabet device ID, za krótki timeout audio, różne domyślne limity PNG,
blokowanie nowego obrazu przez niekończące się ponawianie wyniku 409,
czas lease liczony dopiero po odpowiedzi, nieodnawiana wygasła sesja oraz
wyścig starych odpowiedzi panelu po zmianie telefonu. Aktualny kod należy
oceniać razem z raportami testów i planem HIL, nie na podstawie samego
zgłoszenia lub deklaracji poprawki.

## Dowód wykonywany automatycznie

[`browser-contract-check.json`](browser-contract-check.json) rejestruje
**7 zaliczonych sprawdzeń** aktualnego JavaScriptu w jsdom + realnego HTTP
gatewaya. Odtwarzanie: [`tests/frontend/README.md`](../../tests/frontend/README.md).
W tym teście HTML urządzenia komunikuje się z atrapą API; backend gatewaya
jest rzeczywisty, a sesja jawnie oznaczona jako symulowana.

Próba uruchomienia Chromium przez Playwright nie otworzyła strony:
`process_singleton_posix.cc: socket() failed: Operation not permitted`.
Nie wykonano obejścia tej blokady ani screenshotów udających render.
Ocena renderu pozostaje `NOT_RUN`. HIL również pozostaje `NOT_RUN`.

## Wymagające pomiaru na urządzeniu

Czas callbacku ADB→HTTP, reakcja na odłączenie w jego trakcie, prąd VBUS,
zmiana roli USB-C, AOA na konkretnym OEM i audio wymagają fizycznego testu.
Pause aplikacji zatrzymuje kolejne wejście; nie cofa już wysłanego wejścia
ani nie jest sprzętowym odłączeniem USB. Potwierdzenie już wykonanego kroku
jest nadal wysyłane przed blokadą pause. Timeout sieci i porządkowanie
transferu mogą wydłużyć powrót wywołania. Te granice są uwzględnione w HIL H08.
