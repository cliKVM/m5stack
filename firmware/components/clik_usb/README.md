# USB host: AOA HID i klient ADB

Komponent dla **ESP-IDF 5.4.2 / ESP32-S3**. Jedno zadanie jest właścicielem
wszystkich operacji USB klienta. Osobne zadanie obsługuje zdarzenia biblioteki
hosta. To implementacja firmware, a nie wrapper wokół programu `adb` na PC.

## Integracja

1. Zainicjalizuj NVS i konfigurację płyty.
2. Uruchom Wi-Fi przed `clik_usb_start()`: generowanie trwałego klucza RSA korzysta
   z `esp_fill_random()` i entropii aktywnego radia.
3. Skonfiguruj poprawny tor **host VBUS**. Komponent USB nie ustawia regulatorów,
   ekspandera ani kierunków zasilania konkretnej płyty.
4. Wywołaj `clik_usb_start(&config)` z zadania aplikacji.
5. Wywołuj blokujące operacje z zadania aplikacji/sieci, nigdy z callbacku statusu
   lub callbacku odbierającego dane screenshotu.

`on_status` musi szybko wracać; można w nim skopiować status do kolejki UI/logów.
`clik_usb_capture(sink, context, timeout_ms)` przekazuje kolejne porcje **surowego
PNG**. Callback może stosować ograniczony czasem backpressure sieci, a jego
context musi pozostawać ważny do powrotu `capture`. Żadne zadanie nie buforuje
całego obrazu. Domyślny limit wynosi 16 MiB; aplikacja może ustawić mniejszy.

## Pierwsze połączenie

AOA: zapytanie `GET_PROTOCOL` 51 → rejestracja HID 54 → deskryptory 56 → raporty 57.
Nie wysyłamy `ACCESSORY_START`, nie instalujemy aplikacji, nie wpisujemy PIN-u,
nie włączamy samoczynnie debugowania i nie klikamy okna zaufania RSA.

Klawiatura jest ośmiobajtowym raportem boot keyboard z automatycznym zwolnieniem
klawisza. Obsługiwane kody usage to 0..0x65, osiem bitów modyfikatorów.
Mysz używa względnego X/Y/kółka -127..127 i trzech przycisków. Stan przycisków
jest raportowany bezpośrednio (umożliwia przeciąganie); po dwóch sekundach bez
kolejnego raportu komponent zwalnia przyciski.

ADB: odnalezienie interfejsu `ff/42/01`, dwa endpointy bulk, handshake `CNXN`,
`AUTH TOKEN` → podpis RSA → w razie potrzeby publiczny klucz do zatwierdzenia.
Klucz RSA-2048 jest generowany osobno na każdym urządzeniu i zapisany w NVS
`clik_adb/rsa_der`. Nie ma klucza zaszytego w firmware. Uszkodzony zapis NVS
powoduje błąd inicjalizacji, nie cichą wymianę tożsamości.

Ponowne zgłoszenie Androida po zmianie ustawień USB tworzy nowy
`connection_generation`. AOA jest rejestrowane od nowa; wyszukiwanie ADB nie
zależy od stałego VID/PID. Pole `connection_generation` w akcji może odrzucić
działanie przygotowane dla poprzedniego połączenia.

## Wykonywanie i walidacja

Transport negocjuje payload 4096 bajtów i wersję ADB z sumami kontrolnymi.
Parser sprawdza magic, długość i checksum przed przekazaniem pakietu.
Uszkodzony strumień jest izolowany; wymagane jest ponowne fizyczne połączenie.

* Screenshot: `exec:screencap -p`; brak PTY i konwersji końców linii.
* Akcje: `shell,v2,raw:<zwalidowane polecenie>`; sukces wymaga odebrania ramki
  exit ze statusem 0. Zamknięcie strumienia bez statusu nie oznacza sukcesu.
* PNG: sprawdzana sygnatura, limit długości i końcowy chunk IEND. Nie jest to
  pełna walidacja CRC wszystkich chunków ani dekoder PNG; backend powinien
  sprawdzać kompletny obraz przed przekazaniem go do modelu.
* `tap`, `swipe`, `key`: wyłącznie parametry liczbowe w określonych zakresach.
* `text`: drukowalne ASCII poza `%`; spacje kodowane jako `%s`, cytowanie POSIX.
  Android `input text` nie daje uniwersalnego wpisywania Unicode ani wiernego
  zachowania wszystkich sekwencji `%s`. Te wejścia zwracają błąd. Nie zamieniamy
  po cichu polskich liter ani treści użytkownika.
* `open_url`: tylko HTTP(S), drukowalne ASCII, poprawne cytowanie pojedynczych
  apostrofów; `%` pozostaje dozwolony w adresie URL.

Po timeoutcie kolejki działanie nie jest rozpoczynane. Czas oczekiwania na
zakończenie wywołania może dodatkowo obejmować anulowanie transferu i ograniczony
timeout callbacku sieciowego. Callback/context nigdy nie są używane po powrocie
publicznego wywołania. Transfer w locie nie jest zwalniany ani ponownie używany.

## Walidacja programowa i sprzętowa

`ASAN_OPTIONS=detect_leaks=0 sh tests/native/run.sh` uruchamia natywne testy
fragmentacji, uszkodzonego transportu, deskryptorów i walidacji akcji. Wyłączenie
LeakSanitizera jest potrzebne w środowisku uruchamianym pod ptrace; ASan i UBSan
pozostają aktywne. Parser protokołu nie alokuje pamięci na stercie.

Kompilacja i testy natywne nie potwierdzają działania elektrycznego VBUS,
konkretnego kabla USB-C, zgodności AOA danego telefonu ani interakcji jego OEM
z ADB. Przed wydaniem potrzebne są testy HIL: AOA bez ADB; zgoda RSA; ponowne
podłączenie; screenshot; poprawny i błędny exit; odpięcie w środku obrazu;
restart z zachowaniem klucza; reenumeracja po włączeniu debugowania USB.

## Źródła protokołów

Implementacja własna, Apache-2.0; publiczne specyfikacje i kod referencyjny:

* [ESP-IDF 5.4.2 USB Host](https://docs.espressif.com/projects/esp-idf/en/v5.4.2/esp32s3/api-reference/peripherals/usb_host.html)
* [Android Open Accessory 2.0](https://source.android.com/docs/core/interaction/accessories/aoa2)
* [scrcpy AOA HID](https://github.com/Genymobile/scrcpy/blob/master/app/src/usb/aoa_hid.c)
* [AOSP: format publicznego klucza Android](https://android.googlesource.com/platform/system/core/+/refs/heads/main/libcrypto_utils/android_pubkey.cpp)
* [Android: ADB i screenshots](https://developer.android.com/tools/adb)

