# Opcjonalny test kontraktu paneli

Test uruchamia prawdziwy gateway na losowym lokalnym porcie, tworzy wyłącznie
syntetyczną sesję i wykonuje aktualny `app.js` w DOM jsdom. Sprawdza logowanie,
chroniony PNG, blob, brak tokenu w storage, zamiar, potwierdzenie, lease/wynik,
nowy obraz, flagę capture, zmianę sesji podczas odpowiedzi oraz nagłówki.
Uruchamia też JavaScript z rzeczywistego `firmware/main/local.html`, ale
**jego odpowiedzi urządzenia są symulowane**. ESP-IDF C nie jest wykonywane.

Z katalogu głównego repozytorium, po instalacji zależności gatewaya:

```sh
npm ci --prefix tests/frontend --ignore-scripts
PYTHONPATH=gateway python tests/frontend/test_live_dom.py
```

Użyj Node 24.15+ (testowano 24.22.3) lub innej wersji dopuszczonej przez
`engines` przypiętego jsdom. Node i jsdom są potrzebne tylko do tego testu. Pakiet nie jest zależnością
firmware ani serwera produkcyjnego. `package-lock.json` przypina zależności.
Raport trafia do `docs/analysis/browser-contract-check.json`; dane testu
i tokeny są tymczasowe i nie są wypisywane.

To sprawdzenie zachowania DOM i rzeczywistych żądań HTTP, **bez renderowania
przeglądarki, screenshotu wizualnego i testu telefonu**. Podczas przygotowania
projektu osobna próba Chromium zakończyła się `socket() Operation not permitted`
przed otwarciem strony. Wyników DOM nie wolno przedstawiać jako HIL ani oceny
wyglądu na fizycznym ekranie. Pełny plan HIL jest w
[`docs/hardware-validation.md`](../../docs/hardware-validation.md).
