# clikVM node 0.2 — desktop, VNC/RFB i HTTP API

Wykonawca dla gateway hey Asa. M5Stack może służyć jako mikrofon, głośnik i panel
przypisanego celu, a ten proces działa na wybranym komputerze lub obok usługi sieciowej.
Node wysyła rzeczywistą obserwację, pobiera jedną typowaną akcję z dzierżawą,
wykonuje ją i publikuje nową obserwację. Nie uruchamia dowolnych komend powłoki.

| Backend | Gdzie uruchomić | Obserwacja | Zakres 0.2 |
|---|---|---|---|
| `native` | W sesji użytkownika Windows, macOS lub Linux X11 | PNG głównego ekranu | Kliknięcie, przeciągnięcie, klawisze, ASCII, otwarcie URL |
| `rfb` | Na komputerze z dostępem do skonfigurowanego VNC | PNG rzeczywistego framebuffer | RFB 3.3/3.7/3.8 Raw + DesktopSize, klawiatura i mysz |
| `http-api` | Na komputerze z dostępem do konkretnego API | Obiekt JSON z ustalonego endpointu | Zamknięty katalog operacji ze schematami argumentów |

**Stan weryfikacji:** implementacja i testy TCP są dostępne. Testy fizycznych sesji
Windows/macOS/X11, konkretnych produktów VNC, produkcyjnego Home Assistant i
dostawcy modeli pozostają `NOT_RUN`; raport znajduje się w
[`verification-report.json`](verification-report.json). Integracja modelowa w
testach używa jawnego, deterministycznego serwera testowego.

## Instalacja

Z katalogu głównego repozytorium, Python 3.11 lub nowszy:

```sh
python3 -m venv .venv-node
.venv-node/bin/python -m pip install -e 'agents/desktop[native,rfb]'
cp agents/desktop/examples/native.json node.json
cp agents/desktop/examples/node.env.example node.env
chmod 600 node.env
```

Edytuj `node.json`: ustaw adres HTTPS własnej bramy i unikalny `device_id`.
W `node.env` wpisz przypisany temu urządzeniu `CLIK_NODE_TOKEN`. Brama musi mieć
ten sam identyfikator i token w `CLIK_DEVICE_TOKENS_JSON`. Każdy cel i kontroler
M5Stack otrzymują odrębne dane uwierzytelniania. Nie umieszczaj tokenu operatora
bramy na node.

```sh
.venv-node/bin/clikvm-node --config node.json --env-file node.env doctor
.venv-node/bin/clikvm-node --config node.json --env-file node.env run
```

`doctor` odczytuje ekran lub stan i wypisuje rzeczywisty profil możliwości; nie
wysyła wejścia klawiatury, myszy ani mutacji API. `run --once` wykonuje jedną
iterację protokołu, która może wykonać już zatwierdzoną akcję z bramy.
`Ctrl+C` zatrzymuje proces po bieżącej ograniczonej czasowo operacji.

W Windows użyj `py -3 -m venv .venv-node`, następnie
`.venv-node\Scripts\python.exe` i `.venv-node\Scripts\clikvm-node.exe` zamiast
ścieżek `bin`. Skopiuj przykłady przez `Copy-Item` i ogranicz dostęp do pliku
sekretów do własnego konta. W macOS użytkownik nadaje uprawnienia Accessibility
oraz Screen Recording aplikacji/terminalowi, który uruchamia proces.

Dla samego VNC wystarcza instalacja `'agents/desktop[rfb]'`; dla HTTP API
`agents/desktop`. Backend natywny wymaga aktywnego pulpitu oraz zależności
systemowych PyAutoGUI. Dla Debian/Ubuntu X11 dokumentacja projektu PyAutoGUI
wymienia m.in. `python3-tk` i `python3-dev`; zrzuty wykonuje tu MSS.

## Inne konfiguracje

- [`examples/rfb.json`](examples/rfb.json): VNC przez lokalny tunel, hasło w
  `CLIK_RFB_PASSWORD`; szczegóły i zakres obsługi w [sieciowym KVM](../../docs/network-kvm.md).
- [`examples/http-api.json`](examples/http-api.json): stałe operacje światła
  Home Assistant. Ustaw własny adres, identyfikator encji i
  `CLIK_DEVICE_API_AUTHORIZATION="Bearer ..."` w prywatnym `node.env`.
- [`examples/clikvm-node.service`](examples/clikvm-node.service): opcjonalna
  usługa użytkownika Linux powiązana z sesją graficzną.
- [`compose.yaml`](compose.yaml): headless RFB/API; backend natywny uruchamiaj
  bezpośrednio w sesji pulpitu. Dostosuj `state_dir` do `/state`.

## Testy

```sh
.venv-node/bin/python -m pip install -e '.[dev]'
.venv-node/bin/python -m unittest discover -s tests/desktop -v
```

Testy używają lokalnych serwerów TCP na losowych portach: RFB, API urządzenia,
gateway Uvicorn i model testowy. Nie potrzebują hasła VNC, OQLLM ani fizycznego
M5Stack. Kontrola natywnego OS jest zastąpiona jawną atrapą tylko na granicy API
systemu; ten wynik nie potwierdza działania na fizycznym pulpicie.

Pełny opis uruchomienia, diagnostyki, ograniczeń tekstu, dzierżaw i zachowania po
awarii: [desktop-agent.md](../../docs/desktop-agent.md).
