---
{
  "schema": "wellmanifest.docs/document/v1",
  "id": "clikvm-controller-modes",
  "kind": "information",
  "version": 1,
  "title": "Trzy tryby kontrolera: Android, sieć i USB HID",
  "status": "draft",
  "owner": "clikVM/m5stack",
  "scope": "repository",
  "created": "2026-11-21",
  "updated": "2026-11-21",
  "review_after": "2026-12-21",
  "evidence": ["firmware/main/main.c", "firmware/main/Kconfig.projbuild", "firmware/components/clik_hid/clik_hid.c", "firmware/components/clik_cloud/clik_cloud.c"]
}
---

# Trzy tryby kontrolera w wersji 0.2

Każdy obraz firmware ma jedną osobowość. Wybór ustala się przy kompilacji;
zmiana trybu wymaga wgrania innego obrazu. ESP32-S3 nie przełącza automatycznie
tego samego natywnego portu pomiędzy hostem Androida i urządzeniem HID PC.
Konfiguracja, hasła Wi-Fi i klucz ADB pozostają w NVS; ich kasowanie nie jest
częścią zwykłej zmiany obrazu.
NVS przechowuje jeden zestaw ustawień połączenia. Przy zmianie Android host
na kontroler sieciowy lub odwrotnie wpisz właściwy identyfikator i token
przez ponowną konfigurację (przytrzymanie 7 s); tokeny tych ról są rozłączne.

| Tryb | Rola USB ESP32-S3 | Polecenia i wykonawca | Obserwacja |
|---|---|---|---|
| `android_usb_host` | Host | AOA HID do ręcznego KM; po zgodzie właściciela typowane akcje ADB wykonuje M5 | PNG z autoryzowanego ADB |
| `network_controller` | Stos hosta i urządzenia HID nie jest uruchamiany | M5 przesyła głos/tekst do jawnie przypisanego celu gatewaya; akcje wykonuje desktop bridge, RFB lub adapter API | Obraz lub stan JSON dostarcza przypisany wykonawca |
| `usb_hid_device` | Urządzenie HID; komputer jest hostem | Ręczne KM z lokalnego panelu; dodatkowo sieciowy frontend głosowy do przypisanego wykonawcy | HID nie dostarcza obrazu; kanał sieciowy musi mieć własną obserwację |

Obsługa Windows, macOS i Linux przez desktop bridge zależy od instalacji
i uprawnień tego agenta. Połączenie RFB wymaga serwera RFB na celu; adapter API
wymaga jawnie skonfigurowanych operacji i obserwacji. Samo wpięcie M5 do USB
nie zapewnia obrazu dowolnego komputera, dostępu do ekranu blokady, instalacji
programów ani uprawnień administratora.

## Dziewięć profili

| Profil | Płyta | Osobowość |
|---|---|---|
| `cores3` | M5Stack CoreS3 K128 | Android USB host |
| `lcd154` | Waveshare ESP32-S3-LCD-1.54, 33867 | Android USB host |
| `lcd154-touch` | Waveshare ESP32-S3-Touch-LCD-1.54, 33869 | Android USB host |
| `cores3-network` | CoreS3 K128 | Kontroler sieciowy |
| `lcd154-network` | LCD 33867 | Kontroler sieciowy |
| `lcd154-touch-network` | Touch 33869 | Kontroler sieciowy |
| `cores3-hid` | CoreS3 K128 | USB HID device + kontroler sieciowy |
| `lcd154-hid` | LCD 33867 | USB HID device + kontroler sieciowy |
| `lcd154-touch-hid` | Touch 33869 | USB HID device + kontroler sieciowy |

Po załadowaniu środowiska ESP-IDF 5.4.2:

```sh
./scripts/firmware.sh cores3-network build
./scripts/firmware.sh cores3-network -p /dev/ttyACM0 flash
./scripts/firmware.sh lcd154-hid build
```

Każdy profil korzysta z osobnego `firmware/sdkconfig.<profil>` i katalogu
`firmware/build-<profil>`. Nie kopiuj konfiguracji ani obrazu pomiędzy
płytami. Własne ustawienia edytuj przez `./scripts/firmware.sh <profil> menuconfig`.
Domyślne profile korzystają z HTTPS i wyłączają TTS. Lokalny HTTP do jawnego
prywatnego adresu IPv4 to osobna opcja deweloperska, domyślnie wyłączona.

## Uruchomienie kontrolera sieciowego

1. Uruchom gateway oraz wybranego wykonawcę. Wykonawca musi nawiązać sesję,
   dostarczyć pierwszą obserwację i regularnie pobierać pracę.
2. W konfiguracji gatewaya przypisz jeden identyfikator kontrolera do jednego
   identyfikatora wykonawcy. Użyj osobnego tokenu kontrolera.
3. Wgraj profil `*-network`. Pierwsza konfiguracja Wi-Fi jest taka sama jak
   w [hardware.md](hardware.md): AP, hasło i kod lokalnego panelu na LCD.
4. W formularzu urządzenia/kontrolera wpisz identyfikator kontrolera, jego
   token oraz adres HTTPS gatewaya. Token operatora nie jest akceptowany.
5. Po połączeniu M5 odczytuje przypisany cel co około 2 sekundy. USB i ADB nie
   są warunkiem użycia głosu. Krótkie naciśnięcie/dotyk rozpoczyna nagranie;
   przytrzymanie przez 7 sekund otwiera ponowną konfigurację po restarcie.
6. Lokalny panel pozwala wysłać tekst, zlecić nową obserwację oraz zatrzymać
   kolejne kroki. Stan i historię zadania widać w panelu gatewaya.

Przykład powiązania w chronionym pliku środowiska gatewaya:

```dotenv
CLIK_DEVICE_TOKENS_JSON='{"biurko-pc":"ODREBNY_DLUGI_LOSOWY_TOKEN_WYKONAWCY"}'
CLIK_CONTROLLER_TOKENS_JSON='{"m5-biurko":"ODREBNY_DLUGI_LOSOWY_TOKEN_KONTROLERA"}'
CLIK_CONTROLLER_TARGETS_JSON='{"m5-biurko":"biurko-pc"}'
```

To przykładowe wartości do zastąpienia. Identyfikator wpisywany do M5 to
`m5-biurko`, nie `biurko-pc`. Urządzenie nie wyszukuje losowego komputera,
nie przyjmuje celu podanego przez model i nie przechowuje tokenu operatora.

### Kontrakt kontrolera

Wszystkie żądania używają Bearer tokenu kontrolera. Każdy POST ma odrębny
`Idempotency-Key`. Główny task firmware jest jedynym klientem operacji cloud.

| Metoda i ścieżka po `/v1/controllers/{id}/` | Treść / odpowiedź |
|---|---|
| `GET target` | Cel, `session_id`, `ready`, stan, platforma, transport, rodzaj obserwacji i numer ostatniej obserwacji |
| `POST intent` | `{"text":"…","expected_session_id":"…"}` |
| `POST capture` | `{"expected_session_id":"…"}` |
| `POST cancel` | `{"expected_session_id":"…"}` |
| `POST audio/stt` | Surowy WAV PCM16 mono 16 kHz; odpowiedź JSON `{"text":"…"}` |
| `POST audio/tts` | `{"text":"…"}`; odpowiedź kanoniczny WAV PCM16 mono 16 kHz |

Firmware odczytuje przypisanie przed poleceniem i zachowuje identyfikator sesji
z początku nagrania przez całe STT. Jeżeli wykonawca uruchomi nową sesję w trakcie,
gateway odrzuca stare polecenie kodem HTTP 409 i nagłówkiem
`X-Clik-Error-Code: target_changed`. Zadanie nie przechodzi automatycznie
na nową sesję. Brak wykonawcy albo obserwacji również blokuje zadanie.
Ograniczenia dostępnych akcji wynikają z profilu wykonawcy w gatewayu.

Nagranie trwa domyślnie 8 sekund. Nie ma ciągłego nasłuchu ani wake-word.
Używany jest jeden kanał z dwóch mikrofonów; nie ma AEC ani beamformingu.
Opcjonalne TTS odczytuje krótki komunikat po zaobserwowaniu stanu `done`;
wymaga `CONFIG_CLIK_TTS_ENABLED` oraz rzeczywistej konfiguracji dostawcy
STT/TTS na gatewayu. Ta implementacja nie przechwytuje audio USB telefonu.

Pauza natychmiast blokuje nowe lokalne wejście i wysyła `cancel` do znanej
sesji. Może nie zatrzymać akcji, która już dotarła do celu; gateway zaznacza
niepewny wynik. Błąd sieci pokazuje `STOP NOT CONFIRMED`. Wznowienie dopuszcza
nowy cel użytkownika, ale nie przywraca automatycznie anulowanego zadania.
Kontroler nie zatwierdza ryzykownych akcji ani nie rozstrzyga niepewnego wyniku;
do tego służy uwierzytelniony operator gatewaya.

## USB HID do komputera

Profil `*-hid` używa dwóch interfejsów TinyUSB: klawiatury oraz myszy.
Lokalny panel zachowuje dotychczasowe klawisze, ruch, przyciski i przewijanie.
Raporty są serializowane; klawisz jest zwalniany po wysłaniu, a dodatkowy task
próbuje zwolnić pozostawiony klawisz po 100 ms i przycisk myszy po 2 s.
Przy braku enumeracji/suspend albo po pauzie nowe wejście jest odrzucane.

Najpierw ukończ konfigurację Wi-Fi. Podłącz M5 do natywnego portu danych USB
komputera, a następnie krótko naciśnij przycisk/dotknij ekranu: uruchomi się
urządzenie HID. Firmware pozostawia wyjście VBUS hosta wyłączone i nie
uruchamia stosu hosta USB. Kolejne krótkie naciśnięcia zlecają nagranie głosu
do przypisanego celu sieciowego. Panel KM wymaga kodu z LCD; działa także,
gdy gateway jest niedostępny, po wcześniejszej konfiguracji Wi-Fi.

**USB KM i cel sieciowy to dwa osobne połączenia.** Ta wersja nie udowadnia,
że komputer na kablu USB jest tym samym celem, którego obraz dostarcza agent.
Automatyczne akcje celu sieciowego wykonuje jego bridge, a nie lokalny HID
w M5. Nie zaimplementowano jeszcze bezpośredniej pętli model → raport HID
połączonej z dowodem aktualnego obrazu tego samego fizycznego celu.
Do czasu takiego powiązania HID jest ręcznym narzędziem KM, a sieć dostarcza
osobną funkcję autonomii.

### Zasilanie i hotplug

Domyślny obraz HID zakłada **zasilanie wyłącznie z USB komputera**. Nie podłączaj
w tej konfiguracji bazy DIN, niezależnego BUS ani baterii utrzymującej MCU
przy odpiętym USB. CoreS3 z bazą/akumulatorem oraz Waveshare z akumulatorem mogą
wymagać odłączenia tego źródła albo przygotowania wariantu self-powered.

Dla zasilania niezależnego USB wymaga wykrywania obecności VBUS. Firmware
ma opcje `CONFIG_CLIK_HID_SELF_POWERED` oraz `CONFIG_CLIK_HID_VBUS_GPIO`.
Domyślna wartość GPIO `-1` blokuje start tego wariantu; GPIO19/20 są
odrzucane, ponieważ należą do USB. Należy wybrać rzeczywiście wolny GPIO dla
konkretnej płyty oraz dodać i zakwalifikować zewnętrzny, bezpieczny dla 3,3 V
dzielnik/komparator. Nie wolno podłączać VBUS 5 V bezpośrednio do GPIO.
Projekt nie deklaruje istnienia takiego obwodu na obecnych płytkach.

Weryfikacja obejmuje również rolę CC portu USB-C, brak napięcia zwrotnego,
budżet prądu, odłączenie/ponowne podłączenie, suspend/resume i reset MCU.
Odczyt rejestru wyłączenia USB output nie zastępuje pomiaru.
Wariant HID nie używa toru zewnętrznego hosta z instrukcji Androida.
Port udostępnia natywne USB ESP32-S3; po starcie HID konsola USB Serial/JTAG
jest niedostępna. Logowanie korzysta z UART.

## Walidacja i ograniczenia

Wydanie zawiera rzeczywiste kompilacje ESP-IDF; ich dowody i sumy SHA-256
należą do raportu wydania. Kompilacja nie jest testem elektrycznym ani
potwierdzeniem sterowników systemu operacyjnego. Dla poniższych scenariuszy
wynik fizyczny pozostaje **NOT_RUN**:

| Scenariusz HIL | Oczekiwany pomiar / obserwacja |
|---|---|
| N01: sieć bez USB | Gotowy desktop/RFB/API, odczyt przypisania, PTT → STT → cel → wynik |
| N02: restart podczas STT | Stara sesja odrzucona; brak zadania w nowej sesji |
| N03: pauza w trakcie zadania | Brak kolejnych kroków; widoczny stan niepewnej akcji, jeśli była w locie |
| N04: odłączenie celu/sieci | Brak cichego przeniesienia zadania; powrót po nowej obserwacji |
| H11: enumeracja HID | Osobne klawiatura i mysz w Windows, macOS i Linux |
| H12: zwalnianie wejścia | Brak zablokowanego klawisza/przycisku po timeout, pauzie i reconnect |
| H13: zasilanie / CC / VBUS | Poprawne role oraz brak źródła VBUS z M5 w device mode |
| H14: self-powered | Zweryfikowany monitor VBUS; detach/suspend/resume przy niezależnym zasilaniu |

Nie wykonano testów BIOS/UEFI, preboot, secure desktop ani wszystkich układów
klawiatur. Deskryptory boot keyboard/mouse nie są deklaracją uniwersalnej
zgodności. VID/PID z zależności służą rozwojowi prototypu; komercyjny produkt
wymaga własnej uzgodnionej identyfikacji USB. BLE, natywny klient RDP,
bezprzewodowe ADB, kamera i USB video capture nie są zaimplementowane w tych
nowych trybach.

## Źródła implementacji

- [ESP-IDF 5.4.2 USB Device Stack: TinyUSB, piny USB i self-powered VBUS](https://docs.espressif.com/projects/esp-idf/en/v5.4.2/esp32s3/api-reference/peripherals/usb_device.html).
- [Oficjalny przykład USB HID dla ESP-IDF 5.4.2](https://github.com/espressif/esp-idf/tree/v5.4.2/examples/peripherals/usb/device/tusb_hid).
- [Przypięta zależność esp_tinyusb 1.7.6](https://components.espressif.com/components/espressif/esp_tinyusb/versions/1.7.6). Wersję TinyUSB i hashe transytywne ustala `firmware/dependencies.lock`.
- [Sprzęt i źródła schematów](hardware.md), [walidacja fizyczna](hardware-validation.md), kod komponentów `clik_hid`, `clik_cloud` i `main`.

