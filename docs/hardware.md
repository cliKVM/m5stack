---
{
  "schema": "wellmanifest.docs/document/v1",
  "id": "clikvm-hardware",
  "kind": "information",
  "version": 1,
  "title": "Sprzęt i połączenie hosta USB",
  "status": "draft",
  "owner": "clikVM/m5stack",
  "scope": "repository",
  "created": "2026-11-21",
  "updated": "2026-11-21",
  "review_after": "2026-12-21",
  "evidence": ["firmware/components/clik_board/board_private.h", "firmware/components/clik_board/clik_board.c", "firmware/main/Kconfig.projbuild"]
}
---

# Sprzęt i połączenie hosta USB

Pierwszy prototyp należy uruchomić na posiadanym **M5Stack CoreS3 K128**.
Drugi profil firmware obsługuje **Waveshare ESP32-S3-LCD-1.54, SKU 33867**,
oraz wariant **Touch, SKU 33869**. W obu przypadkach ESP32-S3 jest hostem USB,
a Android urządzeniem USB. Komputer służy do kompilacji i wgrania firmware;
po uruchomieniu nie uczestniczy w komunikacji z telefonem.

To opis implementacji i wymagań połączenia, **nie potwierdzenie działania na
fizycznych płytkach**. Wyniki testów na sprzęcie pozostają `NOT_RUN` w
[hardware-validation.md](hardware-validation.md). Samo wykrycie układu ESP32-S3,
poprawna kompilacja i odczyt rejestru zasilania nie dowodzą poprawnego VBUS.

## Warianty i zakres wsparcia

| Wariant | Profil w repozytorium | Interfejs użytkownika i audio | Warunek dla telefonu |
|---|---|---|---|
| CoreS3 K128 | `firmware/boards/cores3.defaults` | Dotyk 320×240; ES7210, dwa mikrofony; AW88298, głośnik | Skonfigurowane źródło 5 V, poprawny tor USB-C/hosta, pomiar VBUS |
| ESP32-S3-LCD-1.54, 33867 | `firmware/boards/lcd154.defaults` | LCD 240×240; BOOT; ES7210, dwa mikrofony; ES8311 i wzmacniacz | Zewnętrzny, zasilany tor hosta; akumulator płytki nie jest w tej implementacji źródłem VBUS telefonu |
| ESP32-S3-Touch-LCD-1.54, 33869 | `firmware/boards/lcd154-touch.defaults` | Jak wyżej, dodatkowo dotyk CST816 | Ten sam zewnętrzny tor hosta |
| Core2 | Brak | Inny ESP32 i inne audio | USB–UART nie zastępuje natywnego kontrolera hosta; nie wgrywać obrazu CoreS3 |
| ESP32-C6-LCD-1.54 | Brak | Inna rodzina mikrokontrolera | USB Serial/JTAG nie zapewnia USB OTG host wymaganego przez ten projekt |
| ESP32-S3-Zero-M / AtomS3R | Brak kompletnego BSP w tym repo | Inne pamięci, piny i wyposażenie | Wymaga portu BSP, osobnego audio/ekranu zależnie od płytki oraz toru VBUS/CC |

Wyposażenie płyt i ograniczenia mikrokontrolerów opisują
[M5Stack CoreS3](https://docs.m5stack.com/en/core/CoreS3),
[M5Stack Core2](https://docs.m5stack.com/en/core/Core2),
[Waveshare S3 LCD 1.54](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.54)
oraz [Espressif USB Serial/JTAG dla C6](https://docs.espressif.com/projects/esp-idf/en/v5.4.2/esp32c6/api-guides/usb-serial-jtag-console.html).
Nazwę „Core3” w konfiguracji należy zastąpić rzeczywistym oznaczeniem **CoreS3**
z obudowy/PCB. CoreS3-SE, Lite i inne rewizje nie otrzymują automatycznie statusu
zweryfikowanych wariantów.

## Piny używane przez aktualny firmware

Numery w tabeli to **GPIO ESP32-S3**, chyba że wskazano pin ekspandera.
Nie są instrukcją podłączania zewnętrznych przewodów do zamkniętej płytki.
Mapowanie wykonuje komponent `clik_board` na podstawie konfiguracji builda.

| Sygnał | CoreS3 K128 | Waveshare S3 LCD / Touch 1.54 |
|---|---|---|
| USB D− / D+ | GPIO19 / GPIO20 | GPIO19 / GPIO20 |
| I²C SDA / SCL | GPIO12 / GPIO11 | GPIO42 / GPIO41 |
| I²S MCLK | GPIO0 | GPIO8 |
| I²S BCLK / WS | GPIO34 / GPIO33 | GPIO9 / GPIO10 |
| I²S DOUT, ESP → głośnik | GPIO13 | GPIO12 |
| I²S DIN, mikrofony → ESP | GPIO14 | GPIO11 |
| LCD SCLK / MOSI | GPIO36 / GPIO37 | GPIO38 / GPIO39 |
| LCD CS / DC | GPIO3 / GPIO35 | GPIO21 / GPIO45 |
| LCD reset | AW9523 pin 9 | GPIO40 |
| Podświetlenie | AXP2101, sterowanie LDO | GPIO46 |
| Dotyk I²C | adres 7-bit `0x38`; reset AW9523 pin 0 | `0x15`, tylko profil Touch |
| Przycisk BOOT | Nie używany jako przycisk aplikacji; GPIO0 jest MCLK audio | GPIO0, aktywny stan niski |
| Włączenie audio | AXP2101 + AW9523 pin 2 | GPIO7, PA enable |
| USB VBUS output enable | AW9523 **pin 5** | Brak sterowania źródłem hosta w BSP |
| BUS output / boost | AW9523 **pin 1 / pin 15** | Zewnętrzny układ zasilania hosta |

AW9523 i AXP2101 mają w CoreS3 adresy I²C odpowiednio `0x58` i `0x34`.
Pin 15 ekspandera **nie oznacza GPIO15 ESP32**. Sterownik CoreS3 inicjuje
panel ILI9342 przez zgodny sterownik ILI9341; dla wykrytego wariantu z firmware
dotyku `0x12` wybiera sekwencję ILI9342E. Odpowiednie kolory, orientację i dotyk
trzeba sprawdzić dla egzemplarza. Waveshare używa ST7789.

Źródła mapowania: [CoreS3 schematic v1.0](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/490/Sch_M5_CoreS3_v1.0.pdf),
[Espressif CoreS3 BSP](https://github.com/espressif/esp-bsp/tree/master/bsp/m5stack_core_s3),
[schemat Waveshare](https://files.waveshare.com/wiki/ESP32-S3-Touch-LCD-1.54/ESP32-S3-LCD-1.54-Schematic.pdf)
oraz kod `firmware/components/clik_board/`.

## CoreS3: dwa rozłączne warianty źródła energii

### Domyślnie: zewnętrzny BUS 5 V

`CONFIG_CLIK_CORES3_EXTERNAL_BUS_POWER=y` jest domyślnym wyborem. Zasilanie
pochodzi z regulowanego BUS 5 V, np. przez właściwą bazę DIN i jej wejście DC.
Specyfikowane dla DINBase wejście 9–24 V dotyczy **wejścia tej bazy**;
takiego napięcia nie wolno podać bezpośrednio na USB ani pin 5 V kontrolera.
Właściwy dla rewizji zakres wejścia należy sprawdzić w
[dokumentacji zestawu CoreS3](https://docs.m5stack.com/en/core/CoreS3).

Po fizycznym uruchomieniu hosta firmware wyłącza boost (`AW pin 15`), pozostawia
BUS jako wejście (`AW pin 1 = 0`), a następnie włącza wyjście USB (`AW pin 5`).
Odczyt rejestru ekspandera sprawdza wyłącznie zapisany stan sterowania.
Nie mierzy napięcia, prądu, poprawności przewodu ani obecności telefonu.

### Opcjonalnie: akumulator CoreS3

`CONFIG_CLIK_CORES3_BATTERY_POWER=y` wybiera wewnętrzny boost z akumulatora.
W tym wariancie nie należy równocześnie podawać zewnętrznego BUS 5 V.
Kod sprawdza bit obecności akumulatora AXP2101, uruchamia boost, wykonuje
sekwencję wstępnego ładowania BUS i włącza USB output. Sekwencja wynika z
[referencji zasilania M5Unified](https://docs.m5stack.com/en/arduino/m5unified/power_class).

Wybór jest jawny podczas budowania obrazu. Firmware nie odgaduje źródła
zasilania na podstawie samego podłączenia USB. Dostępnego prądu, spadków napięcia,
temperatury i czasu pracy z telefonem nie zmierzono. Wariant z akumulatorem
wymaga osobnej kwalifikacji; pojemność baterii nie jest obietnicą ładowania
telefonu ani określonego czasu działania.

## USB-C: dlaczego sam kabel C–C może nie wystarczyć

Kontroler USB, kierunek danych, CC oraz VBUS są oddzielnymi elementami.
Schematy obu omawianych płyt pokazują rezystory **Rd 5,1 kΩ** na CC.
Uruchomienie stosu USB host ani `USB_OTG_EN` nie zmienia tych rezystorów
w reklamę źródła USB-C. Zwykłe połączenie C–C może ustawić telefon jako hosta
lub nie stworzyć oczekiwanego połączenia.

Tor między płytką a telefonem trzeba zakwalifikować jako kompletny zestaw:

1. D+/D− muszą prowadzić do natywnego kontrolera ESP32-S3; Android ma pełnić
   rolę urządzenia.
2. Telefon musi otrzymać prawidłowe VBUS 5 V ze źródła z ograniczeniem prądu.
3. Adapter/dok musi zapewniać właściwe role CC lub poprawne przejście przez
   interfejs USB-A hosta. Przypadkowa przejściówka opisana „OTG” tego nie dowodzi.
4. Zasilanie nie może cofać się do drugiego źródła ani do komputera używanego
   do programowania. Należy sprawdzić konkretny schemat i przewody.

Nie podano numeru „uniwersalnego kabla”, ponieważ bez pomiaru nie byłaby to
zweryfikowana lista zakupowa. Pierwszy test należy wykonać ze źródłem
z ograniczeniem prądu i miernikiem USB, zachowując dostęp do odłączenia VBUS.

### Waveshare: wymagane zewnętrzne VBUS hosta

W obecnym BSP funkcja `clik_board_host_power()` dla Waveshare **nie steruje
zasilaniem telefonu**. Zwraca powodzenie, ponieważ start stosu jest możliwy,
ale źródło 5 V i rola CC należą do zewnętrznego adaptera/doku. Port płytki
służący do ładowania/programowania i bateria nie potwierdzają możliwości
zasilania telefonu w roli hosta.

Dlatego do kosztu tańszej płytki należy doliczyć dopracowany tor hosta,
okablowanie i obudowę. Programowy przycisk Start nie wyłącza zewnętrznego
zasilacza Waveshare. Ochrona przed dwoma źródłami VBUS musi istnieć w sprzęcie.

## Kolejność pierwszego uruchomienia

1. Sprawdź oznaczenie płyty i rewizję; zbuduj właściwy profil.
2. Wgraj firmware przez komputer. W tym etapie host USB pozostaje wyłączony.
   Ten sam natywny port USB nie może jednocześnie obsługiwać konsoli USB
   komputera i telefonu jako urządzenia hosta; domyślna konsola firmware to UART.
3. Przy pierwszym starcie połącz się z siecią `CLIKVM-xxxx`. Losowe hasło WPA2
   i kod lokalnego panelu są pokazane na LCD. Otwórz `http://192.168.4.1/`.
4. Wpisz Wi-Fi, HTTPS URL wdrożonego gatewaya, identyfikator urządzenia i jego
   osobny token. Dane trafiają do NVS `clik_config`; po zapisaniu następuje restart.
5. Odłącz przewód komputera, zapewnij właściwe zasilanie oraz zweryfikowany tor
   hosta i podłącz odblokowany telefon. Dopiero wtedy fizycznie naciśnij Start:
   dotknij LCD CoreS3 albo krótko naciśnij BOOT Waveshare.
6. Jeśli telefon wspiera AOA HID, użyj lokalnego panelu KM do nawigacji.
   Właściciel telefonu sam włącza debugowanie USB i potwierdza klucz RSA.
   AOA nie omija blokady telefonu ani komunikatu zaufania ADB.
7. Po statusie ADB ready naciśnięcie przycisku/dotyku inicjuje nagranie głosu
   domyślnie przez 8 s. Długie przytrzymanie przez 7 s uruchamia ponowną
   konfigurację sieci po restarcie, zachowując osobny klucz ADB.

Na CoreS3 przed gotowością ADB cztery obszary dotyku wysyłają odpowiednio
Tab / Enter / Escape / kliknięcie; w profilu Waveshare krótki przycisk wysyła
Tab. Pełniejszy ruch myszy i klawiatura są w lokalnym panelu KM. Układ
współrzędnych dotyku musi przejść HIL przed poleganiem na tych skrótach.

## Audio, kamera i obraz telefonu

Audio pochodzi z mikrofonów i głośnika kontrolera przez I²S, nie z mikrofonu
telefonu pobieranego po USB. Sterownik odczytuje dwa kanały ES7210, lecz do STT
wybiera pierwszy kanał. Implementacja nie zapewnia beamformingu, AEC, wake-word
ani ciągłego nasłuchu. Nagranie to WAV PCM16 mono 16 kHz; odtwarzanie TTS jest
opcjonalne (`CONFIG_CLIK_TTS_ENABLED`, domyślnie wyłączone).

Kamera CoreS3 nie jest używana przez tę aplikację. Obraz telefonu pochodzi
z `exec:screencap -p` po autoryzowanym ADB i jest strumieniowany jako PNG.
Bez autoryzowanego ADB AOA HID daje KM, lecz nie daje zrzutu ekranu.
Zewnętrzna kamera byłaby osobnym sposobem obserwacji z własnym montażem,
kalibracją i API. Nie ma jej sterownika ani automatycznego fallbacku w tym
wydaniu. Wymagania AOA i ADB opisują
[Android AOA 2.0](https://source.android.com/docs/core/interaction/accessories/aoa2)
i [Android Debug Bridge](https://developer.android.com/tools/adb).
