---
{
  "schema": "wellmanifest.docs/document/v1", "id": "clikvm-limitations",
  "kind": "information", "version": 2, "title": "Zakres i ograniczenia 0.2",
  "status": "draft", "owner": "clikVM/m5stack", "scope": "repository",
  "created": "2026-11-21", "updated": "2026-11-21", "review_after": "2026-12-21"
}
---

# Zakres i ograniczenia wersji 0.2

## Implementacja a kwalifikacja produktu

Repozytorium zawiera działający serwer, panel, symulator oraz skompilowany kod
ESP32. Nie podłączono fizycznej płytki ani telefonu. Nie zmierzono opóźnień,
poboru prądu, stabilności Wi-Fi/audio, orientacji LCD ani poprawności toru VBUS/CC.
Wszystkie scenariusze HIL pozostają NOT_RUN. Zmiana tego statusu wymaga dowodów
z [procedury kwalifikacji](hardware-validation.md).

## Android i USB

Wybór kanału dla komputerów i API opisuje [universal-system.md](universal-system.md).
Profil platformy nie stanowi deklaracji działania z każdą wersją OS.

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

Dozwolone typy akcji Androida: tap, swipe, key, text, open_url, wait i screenshot.
Kontrakt uniwersalny dodaje ósmą akcję `api`, wyłącznie dla zadeklarowanych operacji.
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

## Komputery, RFB i API

`clikvm-node` implementuje native Windows/macOS/Linux X11, klienta RFB oraz
stałe HTTP API. Testy protokołu i kontrolowanych serwerów TCP nie potwierdzają
działania wszystkich fizycznych pulpitów ani produktów VNC. Agent wymaga
instalacji na kontrolowanym komputerze albo dostępu do istniejącej usługi.
Nie ma automatycznej instalacji po wpięciu M5 w dowolny USB.

Native działa w aktywnej sesji użytkownika. Uprawnienia, skalowanie/DPI,
układ monitorów i blokady pulpitu podlegają kontroli; nieznana geometria
blokuje wejście. macOS wymaga zgód systemowych. Nie ma obsługi portalu Wayland,
secure desktop Windows, ekranu logowania ani BIOS. Native nie deklaruje
ogólnego sterowania wieloma monitorami. Szczegóły: [desktop-agent.md](desktop-agent.md).

RFB obsługuje tylko opisany podzbiór protokołu/uwierzytelnienia. Nie jest
klientem RDP, SPICE ani gotowym adapterem dowolnego produktu KVM. Deklaracja
Androida dla RFB jest odrzucana z powodu niepotwierdzonej semantyki nawigacji.
Chronione połączenie TLS/tunel należy skonfigurować. Szczegóły: [network-kvm.md](network-kvm.md).

HTTP API odczytuje JSON z ustalonego originu i wykonuje nazwane operacje ze
schematami argumentów. Nie tworzy screenshotu i nie daje wizualnego KVM.
Powodzenie odpowiedzi HTTP nie zastępuje ponownego odczytu stanu celu.
Przykładowy Home Assistant wymaga własnej encji, uprawnień i kwalifikacji.

## Kontroler sieciowy i USB HID device

Kontroler sieciowy ma osobny token i statyczne przypisanie do celu w gatewayu.
Nie wyszukuje urządzeń automatycznie i nie wybiera dowolnego celu po nazwie
wypowiedzianej przez użytkownika. Zmiana sesji w trakcie nagrywania blokuje
stare polecenie. Bez obserwacji nie ma autonomicznego planu.

USB HID device oferuje ręczny KM z lokalnego panelu oraz odrębny frontend
głosowy do wykonawcy sieciowego. **Nie ma automatycznego routingu planera
obrazu do surowego HID.** Wymagałby on spójnego powiązania kanałów obrazu
i wejścia. HID nie przechwytuje ekranu PC ani telefonu.

Host Androida i urządzenie HID to osobne obrazy. Nie mogą działać równocześnie
na tym samym wewnętrznym PHY USB. Domyślny HID jest bus-powered; CoreS3
z baterią lub DIN wymaga monitora VBUS i jawnego self-powered builda.
Wszystkie szczegóły oraz scenariusze HIL są w [controller-modes.md](controller-modes.md).

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
w standardowej konfiguracji 0.2.

Kanoniczne POA i Logs oraz lokalne testy przechodzą. Zbiorczy `wellman check`
nie przechodzi w całości: różnice POA checkera, profil Subactor/MCP i wymagania
infrastruktury uprawnień floty pozostają jawnymi lukami. Opcjonalny URTLog
nie jest zaimplementowany bez zweryfikowanego pakietu. [Raport adopcji](standards.md)
podaje dokładne dowody i przypięte źródła.

Najbliższy etap to HIL na posiadanym CoreS3: zasilanie/CC, enumeracja, AOA,
RSA, poprawny screenshot, jeden krok, awarie połączeń i polskie audio.
Dopiero po tej kwalifikacji warto przenosić pokaz na tańszy sprzęt i projektować
obudowę handlową wokół sprawdzonego toru USB.
