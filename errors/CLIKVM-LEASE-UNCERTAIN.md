# CLIKVM-LEASE-UNCERTAIN — niepewny efekt działania

## Rozpoznanie

Urządzenie mogło wykonać kliknięcie, ale nie dostarczyło potwierdzenia albo
restart nastąpił w trakcie działania. Kolejny lease nie powinien oznaczać
automatycznej zgody na powtórzenie nieidempotentnego skutku.

## Postępowanie

Pobierz nową obserwację telefonu, sprawdź stan sesji i rozstrzygnij, czy
działanie wykonano, nie wykonano, czy zadanie należy porzucić. Użyj
udokumentowanego endpointu rozstrzygnięcia i nowej klatki. Nie edytuj ręcznie
zdarzeń w SQLite ani nie kasuj historii, aby wymusić ponowienie.

## Warunek zakończenia

Sesja ma nowy zweryfikowany stan i zapisane rozstrzygnięcie. Wynik niepewny
nie jest raportowany jako sukces i nie znika przez sam restart aplikacji.
