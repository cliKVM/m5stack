---
{
  "schema": "wellmanifest.docs/document/v1", "id": "clikvm-limitations",
  "kind": "information", "version": 1, "title": "Zakres i ograniczenia 0.1",
  "status": "draft", "owner": "clikVM/m5stack", "scope": "repository",
  "created": "2026-11-21", "updated": "2026-11-21", "review_after": "2026-12-21"
}
---

# Zakres i ograniczenia wersji 0.1

## Implementacja a kwalifikacja produktu

Repozytorium zawiera działający serwer, panel, symulator oraz skompilowany kod
ESP32. Nie podłączono fizycznej płytki ani telefonu. Nie zmierzono opóźnień,
poboru prądu, stabilności Wi-Fi/audio, orientacji LCD ani poprawności toru VBUS/CC.
Wszystkie scenariusze HIL pozostają NOT_RUN. Zmiana tego statusu wymaga dowodów
z [procedury kwalifikacji](hardware-validation.md).

## Android i USB

AOA HID zależy od telefonu i jego konfiguracji. Daje wejście klawiatury/myszy
bez uprzedniego ADB, ale nie daje obrazu. Właściciel musi widzieć telefon podczas
pierwszej konfiguracji oraz świadomie zaakceptować zaufanie ADB. Nie ma obejścia
blokady, MDM ani ograniczeń OEM. Nie obiecujemy działania na dowolnym Androidzie.

Zwykły kabel C–C nie potwierdza roli hosta i źródła VBUS. CoreS3 ma sterowanie
zasilaniem, ale jego CC nie staje się automatycznie źródłem USB-C. Waveshare
wymaga zewnętrznego toru hosta/zasilania. Dobór elektryczny opisuje
[hardware.md](hardware.md). USB ESP32-S3 pracuje z prędkością Full Speed;
nie jest nośnikiem USB 3 ani urządzeniem zapewniającym 150 MB/s.

Wydanie obsługuje jeden telefon na kontroler, bez huba wielourządzeniowego.
Wymaga interfejsu ADB i obsługi shell-v2 dla potwierdzania poleceń. Transfer
nieodwołalnie zablokowany po błędzie sterownika może wymagać odłączenia lub
restartu. Klucz RSA jest trwały i nie jest automatycznie kasowany w ramach naprawy.

## Obraz i działania

Obraz to kolejne PNG ze screencap; nie strumień wideo KVM. Nie ma kamery ani
optical fallback. Aplikacje z ochroną przechwytywania mogą dawać pusty/zablokowany
obraz. Android opisuje ograniczenia np. dla
[`FLAG_SECURE`](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#FLAG_SECURE).

Dozwolone typy akcji: tap, swipe, key, text, open_url, wait i screenshot.
Nie ma dowolnego shella, instalacji APK, przesyłania plików MTP, automatycznego
instalatora heyasa na każdej platformie ani protokołu ADB przez BLE/Wi-Fi.
Możliwe jest otwarcie strony HTTP(S), ale decyzje przeglądarki dotyczące instalacji
i uprawnień pozostają częścią Androida.

Android `input text` obsługuje w tym projekcie drukowalne ASCII bez `%`.
Unicode wymaga przyszłego uzgodnionego kanału wejścia; polskie intencje i STT
same w sobie są UTF-8. Nie deklarujemy osobnego API do SMS ani rozmów telefonicznych.
Nawigacja UI zależy od aplikacji i planera. Model może się pomylić, a klasyfikacja
akcji wymagającej zgody nie jest formalną gwarancją.

Pauza zatrzymuje przyszłe kroki, lecz nie cofa już przesłanej akcji i nie
przerywa natychmiast blokującej transmisji USB/STT. Wynik wykonanej akcji jest
potwierdzany również podczas pauzy. Po uncertain wymagane jest rozstrzygnięcie
z aktualnego obrazu; po restarcie nie ma automatycznego odtwarzania kliknięć.

## Audio, modele i sieć

Audio pochodzi z kontrolera. Dwa kanały ES7210 są odczytywane, a do STT wybierany
jest pierwszy. Nie ma beamformingu, AEC, wake-word ani ciągłego nasłuchu.
Push-to-talk domyślnie nagrywa 8 s; ustawialny zakres to 1–15 s.
TTS domyślnie jest wyłączony; po włączeniu mówi raz po zakończeniu zadania.

Publicznie dostępny kontrakt OQLLM nie pozwolił potwierdzić wszystkich usług
obrazu i audio. Adaptery są implementacją tego repozytorium. Testy dostawców
używają kontrolowanych odpowiedzi HTTP; nie wysłano płatnych zapytań i nie
potwierdzono wyników na prywatnym produkcyjnym API użytkownika.

Serwer HTTP na ESP32 służy konfiguracji/AP oraz lokalnej sieci. Jest chroniony
kodem wyświetlanym na LCD, ale nie ma TLS. Nie wystawiaj go na publiczny Internet.
Połączenie firmware→gateway domyślnie jest HTTPS z walidacją CA; tryb HTTP LAN
wymaga jawnego builda developerskiego.

## Operacje, standardy i rozwój

SQLite i blokada stanu wymagają jednego procesu. Media mają limit/retencję,
natomiast niezmienny dziennik wymaga monitorowania rozmiaru i polityki archiwizacji.
Brak OTA, Secure Boot, szyfrowania NVS, zdalnej floty i kont wielu operatorów
w standardowej konfiguracji 0.1.

Kanoniczne POA i Logs oraz lokalne testy przechodzą. Zbiorczy `wellman check`
nie przechodzi w całości: różnice POA checkera, profil Subactor/MCP i wymagania
infrastruktury uprawnień floty pozostają jawnymi lukami. Opcjonalny URTLog
nie jest zaimplementowany bez zweryfikowanego pakietu. [Raport adopcji](standards.md)
podaje dokładne dowody i przypięte źródła.

Najbliższy etap to HIL na posiadanym CoreS3: zasilanie/CC, enumeracja, AOA,
RSA, poprawny screenshot, jeden krok, awarie połączeń i polskie audio.
Dopiero po tej kwalifikacji warto przenosić pokaz na tańszy sprzęt i projektować
obudowę handlową wokół sprawdzonego toru USB.
