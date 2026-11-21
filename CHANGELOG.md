# Zmiany

## 0.2.0 — 2026-11-21

- Wspólny kontrakt możliwości: platforma, transport, obserwacja, akcje, klawisze i operacje API.
- Agent Windows/macOS/Linux X11: rzeczywiste screenshoty oraz wejście klawiatury i myszy.
- Własny klient RFB 3.3/3.7/3.8 do zgodnych serwerów VNC, z ograniczeniami rozmiaru i transportem TLS/tunel.
- Adapter stałego HTTP API: obserwacje JSON i zamknięty katalog operacji ze schematami argumentów.
- Oddzielne tokeny kontrolerów głosowych oraz przypisanie M5Stack do urządzenia docelowego.
- Trzy rozłączne tryby M5Stack: Android USB host, kontroler sieciowy oraz USB HID device; dziewięć profili firmware.
- Prawdziwy TinyUSB HID keyboard/mouse, zwalnianie wejścia i jawne warunki zasilania VBUS.
- Panel rozróżnia screenshot, stan JSON i brak obserwacji; zachowano zgodność klienta Androida 0.1.
- Dziennik wykonawcy desktopowego zabezpiecza przed ponowieniem niepewnego kliknięcia.
- POA: osiem logicznych typów akcji i 15 rekordów katalogu (w tym siedem zgodnych URI Androida).
- Instrukcje instalacji, trybów USB, wyboru kanału, kwalifikacji systemów i planu dalszej rozbudowy.

Wydanie programowe do kwalifikacji. Brak fizycznej walidacji MCU, Androida,
Windows/macOS/X11 i produkcyjnych usług modelowych. HID nie dostarcza obrazu;
Wayland portal, iOS KVM, BLE i sprzętowe przechwytywanie BIOS są poza implementacją.

## 0.1.0 — 2026-11-21

- Firmware ESP-IDF 5.4.2 dla CoreS3 K128 oraz Waveshare LCD/Touch 1.54.
- USB host AOA HID, klient ADB z trwałym RSA, shell-v2 i streaming PNG.
- Konfiguracja Wi-Fi z LCD, lokalny panel KM, push-to-talk oraz opcjonalny TTS.
- Gateway z panelem, typowanym plannerem i konfigurowalnymi adapterami OQLLM/audio.
- Sesje, ograniczone lease, numeracja obrazów, idempotencja i stan uncertain.
- SQLite CQRS/ES, odtwarzanie pięciu projekcji i audyt Wellmanifest Logs.
- Kanoniczne schematy POA/Logs, przypięte źródła i raport luk Wellmana.
- Symulator HTTP, testy natywne/API/UI, buildy i procedura HIL.

Pierwsze wydanie programowe. Nie wykonano kwalifikacji fizycznego zestawu;
nie deklaruje się zgodności z dowolnym Androidem ani pełnej adopcji floty Wellman.
