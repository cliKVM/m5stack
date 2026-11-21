# Zasady pracy w clikVM/m5stack

Repozytorium zawiera runtime kontrolera Androida i jego gateway. Zachowuj
oddzielenie firmware, protokołu, API, operatora i usług modelowych.

## Zmiany

- Używaj ESP-IDF 5.4.2 i jawnych profili sprzętu. Nie przenoś pinów pomiędzy płytami.
- Po zmianie modeli akcji uruchom `python scripts/standards.py generate`, sprawdź
  diff katalogu POA i zgodność firmware. Nie dopisuj fikcyjnych możliwości.
- Nie usuwaj świadomie przyjętych kanonicznych pól schematów dla obejścia starego
  checkera. Lukę upstream opisz w `docs/standards.md` i raporcie.
- Mutacje muszą zachować idempotencję, dzierżawę, kontrolę generacji USB i numeru
  klatki. Nie powtarzaj akcji, której wynik jest niepewny.
- Plan modelu nie daje uprawnienia do dowolnego shella. Zachowaj listę typowanych akcji.
- Nie loguj tokenów, hasła Wi-Fi, klucza prywatnego RSA ani surowych danych w audycie
  Logs. Prywatny dziennik domenowy pozostaje odrębnym, chronionym zbiorem.
- Nie zatwierdzaj zdalnie zgody ADB, nie obchodź blokady telefonu. Zaufanie ustanawia
  właściciel na urządzeniu.
- Rutynowe działania w zakresie zadanego celu są automatyczne. Nie dodawaj
  potwierdzenia każdego kliknięcia jako domyślnego trybu.
- Nie nadpisuj istniejącego `.env`, NVS, bazy ani klucza ADB podczas naprawy.

## Dowody

`make test` uruchamia testy serwera, audytu i parserów; `make standards` sprawdza
przypięte kontrakty. Zmiany C sprawdzaj przez właściwy pełny build firmware.
Aktualizuj dokumentację i `docs/validation.md` zgodnie z faktycznymi wynikami.
Symulację, test hosta, kompilację, test API dostawcy i HIL rozróżniaj jawnie.
Nie oznaczaj `NOT_RUN` jako `PASS` bez wykonania oraz identyfikowalnego dowodu.

## Publikacja

`.env`, `.state`, SDK, managed components i pośrednie buildy nie należą do Git.
Wydanie źródeł/binariów przygotowuje `scripts/package_release.py` z SHA-256.
Nie deklaruj ustawionej ochrony gałęzi, opublikowanej domeny ani udanego push,
jeżeli nie zostały rzeczywiście potwierdzone. Docelowy origin to
`https://github.com/clikVM/m5stack.git`.
