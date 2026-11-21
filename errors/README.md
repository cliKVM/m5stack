# Katalog operacyjny i runbooki clikVM

Kanoniczny audyt `wellmanifest.logs/event/v1` używa obecnie `code: null`.
Poniższe identyfikatory oznaczają runbooki dokumentacji; gateway nie deklaruje
ich automatycznej emisji jako błędów upstream. Rejestrację konkretnego kodu
w audycie należy poprzedzić zamkniętym katalogiem błędów i walidacją
`logs_check.py error-adoption` przypiętego standardu.

| Runbook | Objaw | Działanie |
| --- | --- | --- |
| [CLIKVM-USB-001](CLIKVM-USB-001.md) | Brak enumeracji lub brak autoryzowanego ADB | Sprawdź fizyczny tor hosta i zgodę Androida |
| [CLIKVM-LEASE-UNCERTAIN](CLIKVM-LEASE-UNCERTAIN.md) | Zgubiony wynik lub niepewność wykonania | Nowa obserwacja i jawne rozstrzygnięcie; bez automatycznego powtórzenia |
| [CLIKVM-UPSTREAM-001](CLIKVM-UPSTREAM-001.md) | Model/audio nie skonfigurowane lub niedostępne | Sprawdź jawny kontrakt usługi, status i konfigurację; nie loguj kluczy |

Zmiana kodu HTTP/komunikatu nie jest sukcesem operacji urządzenia. O wyniku
decydują utrwalony wynik wykonania i późniejsza obserwacja.
