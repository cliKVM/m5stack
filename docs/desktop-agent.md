# Agent desktopowy hey Asa / clikVM 0.2

## Rola i zakres

`agents/desktop` jest rzeczywistym wykonawcą działań na komputerze użytkownika.
Udostępnia gateway obraz głównego monitora oraz ograniczony katalog wejścia
klawiatury i myszy. M5Stack może pozostać niewielkim kontrolerem głosowym:
połączenie Wi-Fi prowadzi do gateway, a akcje trafiają do wcześniej przypisanego
komputera. W takim wariancie USB i ADB nie są potrzebne do sterowania PC.

Proces uruchamia właściciel urządzenia w swojej sesji. Nie otwiera lokalnego
portu sterującego: pobiera akcje przez wychodzące połączenie do skonfigurowanej
bramy. Klucze usług modelowych pozostają w bramie. Uprawnienia systemowe,
logowanie do sesji oraz zaufanie do kontrolera ustanawia człowiek.

## Macierz obsługi

| Cel | Implementacja | Obserwacja i wejście | Ograniczenia / dowód |
|---|---|---|---|
| Windows w aktywnej sesji | `native` | MSS PNG, PyAutoGUI klawiatura/mysz, systemowy launcher URL | Kod dostępny; rzeczywisty Windows `NOT_RUN`; bez UAC secure desktop i ekranu logowania |
| macOS w aktywnej sesji | `native` | MSS PNG, PyAutoGUI, `open` dla URL | Wymaga Accessibility i Screen Recording; fizyczny macOS `NOT_RUN` |
| Linux X11 | `native` | MSS PNG, PyAutoGUI, `xdg-open` | Wymaga `DISPLAY` i dostępu do sesji; fizyczny X11 `NOT_RUN` |
| Linux Wayland | Ten backend odmawia startu | Należy użyć zatwierdzonej przez użytkownika usługi zdalnego pulpitu | Portal RemoteDesktop nie został zaimplementowany w 0.2 |
| Urządzenie z RFB/VNC | `rfb` | Rzeczywisty framebuffer i wejście RFB | Testowany protokół TCP; zgodność z konkretnym produktem wymaga odbioru |
| Urządzenie z REST API | `http-api` | JSON stanu i ustalone operacje | Nie daje obrazu pulpitu; szczegóły w `network-kvm.md` |
| iOS/iPadOS | Brak natywnego agenta | Tylko możliwości legalnie udostępnionego API/usługi | Nie deklarujemy uniwersalnego KVM |
| BIOS, wyłączony OS, brak usługi obrazu | Brak tego kanału | Potrzebny odpowiedni sprzętowy KVM i dostęp do obrazu | Samo USB HID nie dostarcza obrazu |

MSS dokumentuje przechwytywanie ekranów, a PyAutoGUI obsługę klawiatury i myszy
Windows/macOS/Linux. W tym projekcie wybór głównego monitora, skalowanie,
ograniczenia transportu oraz zgłoszenie uprawnień są osobną warstwą.
Źródła: [MSS](https://python-mss.readthedocs.io/stable/),
[PyAutoGUI](https://pyautogui.readthedocs.io/en/latest/),
[portal RemoteDesktop](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html).

## Instalacja i pierwsze połączenie

Z katalogu głównego repozytorium:

```sh
python3 -m venv .venv-node
.venv-node/bin/python -m pip install -e 'agents/desktop[native,rfb]'
cp agents/desktop/examples/native.json node.json
cp agents/desktop/examples/node.env.example node.env
chmod 600 node.env
```

1. Ustaw `gateway_url` na HTTPS własnego gateway. Lokalny test HTTP wymaga
   jawnego `allow_http_gateway: true` i adresu localhost albo prywatnego IP.
2. Ustaw jednoznaczny `device_id`, np. `office-pc`. Ten sam identyfikator i
   odrębny losowy token dodaj w konfiguracji gateway
   `CLIK_DEVICE_TOKENS_JSON`.
3. W prywatnym `node.env` wpisz `CLIK_NODE_TOKEN`. Token musi mieć minimum
   24 znaki i nie może być tokenem operatora ani innego urządzenia.
4. Uruchom `doctor`, przeczytaj wynik i przyznaj brakujące uprawnienia lokalnie.
5. Uruchom `run`. W gateway przypisz kontroler M5Stack do `office-pc` przez
   `CLIK_CONTROLLER_TARGETS_JSON`, używając osobnego tokenu kontrolera.

```sh
.venv-node/bin/clikvm-node --config node.json --env-file node.env doctor
.venv-node/bin/clikvm-node --config node.json --env-file node.env run
```

Windows PowerShell:

```powershell
py -3 -m venv .venv-node
.venv-node\Scripts\python.exe -m pip install -e 'agents/desktop[native,rfb]'
Copy-Item agents/desktop/examples/native.json node.json
Copy-Item agents/desktop/examples/node.env.example node.env
# Edytuj konfigurację oraz token; zachowaj prywatność pliku node.env.
.venv-node\Scripts\clikvm-node.exe --config node.json --env-file node.env doctor
.venv-node\Scripts\clikvm-node.exe --config node.json --env-file node.env run
```

W macOS włącz dostęp dla właściwego interpretera/aplikacji terminala w
Ustawieniach systemowych → Prywatność i ochrona → Dostępność oraz Nagrywanie
ekranu. Po zmianie uruchom proces ponownie. `doctor` sprawdza dostęp przed
rejestracją sesji. Nie zatwierdza zgody automatycznie.

Na Linux wymagany jest aktywny X11 i zależności pakietu PyAutoGUI. Skorzystaj z
[instrukcji instalacji projektu](https://pyautogui.readthedocs.io/en/latest/install.html)
odpowiedniej dla dystrybucji. Nie uruchamiaj natywnego node jako usługi systemowej
root bez kontekstu pulpitu.

## Diagnostyka i eksploatacja

`doctor` wykonuje prawdziwą obserwację, po czym wypisuje JSON zawierający
`profile`, `capabilities`, rodzaj obserwacji, rozmiar obrazu oraz
`input_probe: NOT_EXECUTED`. Pole `hardware_validated: false` przypomina, że
udany zrzut nie potwierdza jeszcze klikania na danej maszynie. Polecenie nie
potrzebuje tokenu gateway, ale VNC/API może potrzebować swoich danych dostępu.

`run --once` może wykonać akcję już oczekującą w bramie; nie jest odpowiednikiem
diagnostyki. `Ctrl+C` i `SIGTERM` kończą pracę po bieżącej operacji. W natywnym
backendzie włączony pozostaje PyAutoGUI FAILSAFE: przeniesienie wskaźnika do
narożnika głównego ekranu przerywa następne wywołanie tego API. Stan niepewny
po takim przerwaniu wymaga obejrzenia nowej obserwacji i decyzji operatora.

W `examples/clikvm-node.service` znajduje się jednostka użytkownika Linux.
Oczekuje interpretera w `~/.local/share/clikvm-node/venv`, konfiguracji
`~/.config/clikvm/node.json` i prywatnego `node.env`. Po przygotowaniu tych
plików skopiuj jednostkę do `~/.config/systemd/user/`, wykonaj
`systemctl --user daemon-reload`, potem
`systemctl --user enable --now clikvm-node.service`. Sesja graficzna musi
udostępniać jednostce `DISPLAY`/`XAUTHORITY`; dostępność zależy od menedżera
pulpitu. Ten autostart nie został sprawdzony na fizycznym komputerze.

Nie usuwaj katalogu stanu, aby „naprawić” błąd niepewnego wykonania. Zawiera
zapis dzierżawy potrzebny do rozstrzygnięcia, czy akcja mogła już się odbyć.
Jednocześnie tylko jeden proces może posiadać ten katalog. Powiązanie z
gateway i `device_id` jest sprawdzane przy starcie.

## Współrzędne, tekst i otwieranie stron

Node przechwytuje główny monitor na początku układu współrzędnych. PNG jest
skalowany do `max_width`, domyślnie 1920 pikseli; współrzędne planera odnoszą się
do tego PNG. Wejście jest przeliczane na logiczne współrzędne systemu, również
gdy skala Retina/DPI różni się od wymiarów obrazu. Zmiana rozmiaru logicznego
ekranu wymaga ponownego otwarcia sesji. W X11 wymiar głównego obrazu musi być
równy wymiarowi root window zwracanemu przez PyAutoGUI; układ obejmujący kilka
monitorów o większym wspólnym obszarze jest blokowany przed rejestracją i
wejściem. Nie deklarujemy obsługi wielomonitorowej ani wszystkich konfiguracji
skalowania bez testu na danej maszynie.

Typy akcji: `tap`, `swipe`, `key`, `text`, `open_url`, `wait`, `screenshot`.
W desktopie `HOME` oznacza klawisz Home, `BACK` skrót powrotu aplikacji lub
przeglądarki, a `APP_SWITCH` przełączanie aplikacji. Nie są to androidowe
przyciski systemowe. Wspólny profil tekstu 0.2 przyjmuje wyłącznie drukowalne
ASCII bez `%`; polskie znaki w poleceniach głosowych są dopuszczalne, ale ich
bezpośrednie wpisywanie wymaga przyszłego adaptera Unicode/IME. Nie ma cichej
transliteracji.

`open_url` przyjmuje tylko zwykły URL HTTP(S), bez danych logowania i znaków
sterujących. Windows używa systemowego `startfile`, macOS ustalonego
`/usr/bin/open`, Linux ustalonego `xdg-open`. Argumenty nie przechodzą przez
powłokę. Model nie wybiera programu, skryptu ani argumentów wykonawczych.

## Cykl akcji, świeżość i awarie

1. Node sprawdza rzeczywistą możliwość obserwacji i rejestruje niezmienny
   `profile` sesji wraz z jawną listą możliwości.
2. Wysyła PNG z rosnącym `frame_id`. Brama planuje względem konkretnego obrazu;
   przy poleceniu z kontrolera wymusza nową obserwację przed planowaniem.
3. Pobiera jedną akcję, jej `lease_id`, `based_on_frame` i pozostały czas.
   Node odejmuje czas żądania od budżetu; odrzuca klatkę inną niż lokalna,
   wygasłą dzierżawę i akcję spoza profilu.
4. Przed efektem zapisuje i synchronizuje dziennik `executing`. Po wykonaniu
   zapisuje wynik i potwierdza go z niezmiennym kluczem idempotencji.
5. Dopiero nowa obserwacja pozwala plannerowi ocenić osiągnięcie celu. Sam
   status `succeeded` oznacza zakończenie wywołania wykonawcy.

Zerwane HTTP po zapisanym wyniku prowadzi do ponowienia tego samego
potwierdzenia. Nie powoduje ponowienia kliknięcia. Restart z wpisem
`executing` zmienia wynik na `uncertain`, nawet jeśli akcja mogła nie zdążyć się
rozpocząć. Tego wykonania node nie powtarza. Terminalny konflikt dzierżawy
zostawia w historii informację `terminal_conflict_review_required`.

Dziennik node przechowuje identyfikatory oraz status, bez zrzutów ekranu,
wpisywanego tekstu i sekretów. Obserwacje przechodzą do gateway i do wybranego
planera zgodnie z konfiguracją. Nie jest to system przetwarzający obraz tylko
lokalnie, jeżeli gateway korzysta ze zdalnego modelu.

## Odbiór i stan dowodów

Uruchom `python -m unittest discover -s tests/desktop -v` w środowisku z
zainstalowanymi `agents/desktop[rfb]` i `.[dev]`. Testy obejmują prawdziwe sockety
RFB i HTTP, właściwy gateway z Uvicorn, utratę odpowiedzi potwierdzającej,
restart, kontrolę klatki i dzierżawy oraz ograniczenia protokołu. Model jest
kontrolowanym serwerem testowym. W teście natywnym prawdziwa jest logika
`NativeBackend`, natomiast granica wywołania API systemu pozostaje atrapą.

Przed pilotem na każdej konfiguracji należy osobiście sprawdzić: zrzut głównego
monitora, kliknięcie niewrażliwego przycisku, powrót po zmianie rozmiaru,
przeciągnięcie, Esc/Tab, otwarcie strony, utratę sieci i zatrzymanie. Tych
wyników nie zastępuje test protokołu. Udokumentuj wersję OS, skalę DPI i nadane
uprawnienia; rzeczywiste HIL pozostaje `NOT_RUN` do tego odbioru.
