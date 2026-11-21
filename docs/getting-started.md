---
{
  "schema": "wellmanifest.docs/document/v1", "id": "clikvm-getting-started",
  "kind": "instruction", "version": 1, "title": "Pierwsze uruchomienie",
  "status": "draft", "owner": "clikVM/m5stack", "scope": "repository",
  "created": "2026-11-21", "updated": "2026-11-21", "review_after": "2026-12-21"
}
---

# Pierwsze uruchomienie

Ta instrukcja prowadzi przez bazowy wariant Android USB host oraz symulator.
Dla komputera wybierz [agenta desktopowego](desktop-agent.md) i
[kontroler sieciowy](controller-modes.md). Wszystkie ścieżki, wymagania i
etapy wdrożenia porównuje [system wielourządzeniowy](universal-system.md).

## 1. Najpierw uruchom serwer i sprawdź pętlę HTTP

W katalogu repozytorium wykonaj `make setup`, `make init-demo`, a następnie
`make serve`. Drugi terminal: `make simulate`. Panel jest pod
`http://127.0.0.1:8787/`, dokumentacja interaktywna pod `/docs`, health pod `/health`.
Pole `event_export` w health informuje, czy projekcja logów jest aktualna.

Generator `.env` zapisuje osobny token urządzenia i operatora. Otwórz plik
lokalnie w edytorze, skopiuj tylko `CLIK_OPERATOR_TOKEN` do panelu. Dla firmware
potrzebna będzie wartość przypisana do `cores3-01` w `CLIK_DEVICE_TOKENS_JSON`.
Nie kopiuj do formularza całego JSON ani cudzysłowów.

Symulator wysyła prawdziwe żądania HTTP, ale generuje obraz i potwierdza wyłącznie
skryptową akcję HOME. Wynik `real_hardware_tested:false` jest zamierzony.
Nie wpisuj losowych celów do demonstracji jako testu zdolności LLM. Przykład
dłuższej sesji do oglądania panelu:

```sh
.venv/bin/clikvm --env-file .env simulate --device cores3-01 \
  --url http://127.0.0.1:8787 --hold-seconds 300
```

## 2. Podłącz prawdziwy model i audio

W `.env` wpisz rzeczywisty `CLIK_MODEL_ENDPOINT`, `CLIK_MODEL_NAME` i klucz.
Model musi przyjmować obraz w wiadomości `image_url` oraz zwracać odpowiedź
JSON zgodną z kontraktem `Plan`. Adapter wymaga endpointu zgodnego z chat
completions; sam katalog modeli lub endpoint POA nie jest zamiennikiem tego API.
Zrestartuj gateway po zmianie konfiguracji.

Przykładowa odpowiedź planera:

```json
{
  "status": "action",
  "reason": "Otwieram ekran główny przed kolejną obserwacją.",
  "requires_confirmation": false,
  "action": {"type": "key", "key": "HOME"}
}
```

Do STT wymagane są `CLIK_STT_ENDPOINT` i opcjonalny model/klucz. Kontrakt
upstream: multipart zawierający WAV i pole `model`, wynik JSON `text`.
Do TTS wymagane są `CLIK_TTS_ENDPOINT`, model, voice i klucz; wyjście dostawcy
musi być WAV PCM, które gateway przelicza do PCM16 mono 16 kHz.
Wersja 0.1 nie deklaruje działającej usługi audio pod domyślnym adresem OQLLM.

Ustaw `CLIK_ALLOW_SIMULATION=0` dla urządzenia fizycznego. W razie lokalnego
modelu dostępnego po HTTP świadomie ustaw `CLIK_ALLOW_HTTP_UPSTREAMS=1`.
Ten przełącznik dotyczy połączenia serwer→model, nie ESP32→gateway.

## 3. Przygotuj dostęp sieciowy dla ESP32

`127.0.0.1` komputera nie jest adresem serwera z punktu widzenia płytki.
Domyślny firmware wymaga publicznie zaufanego certyfikatu HTTPS i poprawnego czasu
z SNTP. Skonfiguruj domenę, TLS i dostęp do gatewaya według [deployment.md](deployment.md).
W formularzu płytki wpisz bazowy adres, np. `https://kontroler.twojadomena.pl`,
bez dopisywania `/v1` i bez końcowego `/`.

Dla krótkiego testu w zaufanej sieci lokalnej można w `menuconfig` włączyć
`CONFIG_CLIK_ALLOW_HTTP_LAN`. Akceptowane są wtedy tylko jawne prywatne adresy
IPv4, np. `http://192.168.1.20:8787`; nazwa `.local`, publiczny IP i HTTP przez
DNS pozostają odrzucone. Uruchom serwer z `--host 0.0.0.0`, ograniczając dostęp
do właściwej sieci. Dostarczone standardowe binaria mają ten tryb wyłączony.

## 4. Zbuduj właściwy firmware

Zainstaluj ESP-IDF v5.4.2 zgodnie z
[instrukcją Espressif dla ESP32-S3](https://docs.espressif.com/projects/esp-idf/en/v5.4.2/esp32s3/get-started/).
Repozytorium SDK musi zawierać jego submoduły. Po `install.sh esp32s3` załaduj
`export.sh` w tym samym terminalu.

```sh
bash scripts/firmware.sh cores3 build
bash scripts/firmware.sh lcd154 build
bash scripts/firmware.sh lcd154-touch build
```

Buduj profil swojego urządzenia; wszystkie trzy polecenia służą macierzy CI.
Każdy profil ma własny build i `sdkconfig`, aby warianty nie dziedziczyły
przypadkowo konfiguracji innej płytki.

```sh
bash scripts/firmware.sh cores3 menuconfig
bash scripts/firmware.sh cores3 -p /dev/ttyACM0 flash
```

Ścieżka portu jest przykładowa. Wskaż port z własnego systemu. Standardowy obraz
CoreS3 zakłada zewnętrzny BUS 5 V, np. z odpowiednio zasilanej DINBase.
Jeśli zasilasz hosta z akumulatora, wybierz w menu `clikVM` wariant
`Internal battery boost, NO external BUS supply`, zapisz i zbuduj ponownie.
Nie podłączaj równolegle źródeł zabronionych przez dokumentację toru zasilania.

W `clikVM` ustawisz też długość nagrania, limit PNG, opcjonalne TTS i tryb HTTP LAN.
Zmiana limitu PNG powinna mieć odpowiednik `CLIK_MAX_FRAME_BYTES` na serwerze.

## 5. Wgrywanie gotowych plików z paczki

Paczka wydania zawiera katalog `binaries/<profil>/`, pliki oraz SHA-256.
W jego środku użyj środowiska ESP-IDF 5.4.2:

```sh
python -m esptool --chip esp32s3 --port /dev/ttyACM0 --baud 460800 \
  write_flash @flash_args
```

`flash_args` pochodzi z rzeczywistego builda; zachowaj jego strukturę podkatalogów.
Offsety to bootloader `0x0`, partition table `0x8000`, aplikacja `0x20000`.
Nie wykonuj `erase_flash` jako rutynowej aktualizacji: usunęłoby to Wi-Fi,
token urządzenia i zaufany klucz ADB. NVS nie jest częścią dostarczonych obrazów.
Partycjonowanie przewiduje jeden obraz factory, bez OTA w tym wydaniu.

Natywny port ESP32-S3 będzie później hostem telefonu. Domyślna konsola aplikacji
to UART, nie jednoczesny USB Serial/JTAG. Jeśli komputer nie wykrywa portu przy
następnym flashowaniu, uruchom sprzętowy tryb download według instrukcji producenta
konkretnej płytki. Nie zakładaj tych samych przycisków i złączy na każdym modelu.

## 6. Konfiguracja na LCD i pierwsza autoryzacja Androida

Na ekranie pojawi się SSID `CLIKVM-...`, losowe hasło i kod sterowania.
Połącz komputer lub telefon konfiguracyjny z AP i otwórz `http://192.168.4.1/`.
Wpisz kod, Wi-Fi, bazowy HTTPS URL, device ID i token tego urządzenia.
Po zapisaniu kontroler restartuje się i łączy z Wi-Fi. Nie wypisuje sekretów
do logów. Kod lokalnego panelu zmienia się przy kolejnym uruchomieniu.

Odłącz przewód programowania. Dopiero po sprawdzeniu zasilania VBUS oraz ról CC
połącz odblokowanego Androida i uruchom hosta krótkim dotknięciem CoreS3 lub
przyciskiem BOOT Waveshare. Adres panelu lokalnego odczytaj z LCD.
W panelu KM wpisz kod z wyświetlacza; poruszaj względną myszą lub użyj klawiszy.

Przy wyłączonym ADB AOA może dawać klawiaturę i mysz, ale nie podgląd ekranu.
Telefon należy widzieć bezpośrednio. Właściciel włącza opcje programistyczne,
debugowanie USB i zatwierdza klucz RSA. Dokładne menu zależy od Androida/OEM.
Program nie omija kodu blokady ani tego dialogu. Po zaakceptowaniu ADB kontroler
tworzy sesję i wysyła pierwszy screenshot.

Wbudowane gesty przed gotowością ADB: CoreS3 górny lewy obszar Tab, górny prawy
Enter, dolny lewy Escape, dolny prawy kliknięcie. Waveshare BOOT wysyła Tab.
Po gotowości ADB krótkie naciśnięcie nagrywa głos. Przytrzymanie przez 7 s
kasuje konfigurację Wi-Fi/token/URL i restartuje setup, zachowując osobny klucz ADB.

## 7. Pierwsze zadanie i weryfikacja

Na testowym telefonie zadaj prosty cel, np. „Otwórz stronę heyasa.com”.
Oglądaj kolejność: cel, plan, wykonanie, nowy screenshot, następny krok lub done.
Do tekstu w polu aplikacji obsługiwane jest obecnie drukowalne ASCII bez `%`.
Polskie cele i transkrypcja są obsługiwane jako UTF-8, lecz nie gwarantują
wpisywania polskich znaków przez Android `input text`.

Panel ma Zatrzymaj, odświeżenie obrazu i rozstrzygnięcie niepewnego wykonania.
Pauza blokuje kolejny krok; nie cofa trwającej transmisji ani wykonanej akcji.
Po utracie potwierdzenia akcja nie jest ponawiana. Sprawdź świeży obraz i zamknij
niepewną próbę; w razie potrzeby zadaj nowy cel.

Jeśli TTS jest włączony i skonfigurowany, kontroler wypowie krótki komunikat
po stanie done. Nie mówi w środku aktywnej dzierżawy akcji.
Pełna kwalifikacja przed pokazem: [hardware-validation.md](hardware-validation.md).
