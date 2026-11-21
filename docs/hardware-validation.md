---
{
  "schema": "wellmanifest.docs/document/v1",
  "id": "clikvm-hardware-validation",
  "kind": "instruction",
  "version": 1,
  "title": "Procedura walidacji fizycznego zestawu",
  "status": "draft",
  "owner": "clikVM/m5stack",
  "scope": "repository",
  "created": "2026-11-21",
  "updated": "2026-11-21",
  "review_after": "2026-12-21",
  "evidence": ["docs/hardware.md", "firmware/components/clik_usb/README.md"]
}
---

# Walidacja na sprzęcie — HIL

**Nie wykonano testów fizycznej płytki ani telefonu. Wszystkie poniższe
przypadki mają status `NOT_RUN`.** Kompilacja, testy parsera na komputerze,
symulator i test HTTP nie zmieniają tego statusu. HIL oznacza tutaj
hardware-in-the-loop: rzeczywisty firmware, przewód, zasilanie i telefon.

Przed pokazem dla klienta należy przejść całą procedurę na dokładnie tym
zestawie, który będzie używany. Powtórz kwalifikację po zmianie płyty,
rewizji, adaptera, źródła zasilania, telefonu lub istotnej wersji Androida.

## Karta zestawu

W raporcie zapisz następujące dane. Nie umieszczaj tokenów, klucza prywatnego
ADB, haseł Wi-Fi ani prywatnych danych użytkownika.

| Pole | Wartość przed rozpoczęciem |
|---|---|
| Data, osoba wykonująca test | `NOT_RECORDED` |
| Commit projektu, profil, SHA256 firmware | `NOT_RECORDED` |
| Model i rewizja PCB, LCD, podstawa | `NOT_RECORDED` |
| Wariant źródła: external BUS / battery / zewnętrzny dok | `NOT_RECORDED` |
| Model zasilacza, ograniczenie prądu, adapter, przewód | `NOT_RECORDED` |
| Telefon, Android build, ustawiony rozmiar i orientacja ekranu | `NOT_RECORDED` |
| Gateway wersja, konfiguracja limitów, model/STT/TTS | `NOT_RECORDED` |
| Dowody: log UART, log serwera, zdjęcia i pomiary | `NOT_RECORDED` |

Użyj testowego telefonu z nieprywatnym ekranem. Pierwsze badanie zasilania
wykonaj z ograniczeniem prądu i możliwością natychmiastowego odłączenia VBUS.
Zasady połączenia i rozdzielenia źródeł są w [hardware.md](hardware.md).

## Macierz wykonania

Wpis `PASS` wymaga konkretnego dowodu i identyfikacji zestawu; puste pole
nie oznacza zaliczenia. Oczekiwane wyniki są kryteriami testu, nie wynikami
już uzyskanymi.

| ID | Sprawdzenie | CoreS3 external BUS | CoreS3 battery | LCD 1.54 + host dok | Dowód |
|---|---|---|---|---|---|
| H01 | Zasilanie, VBUS, CC i odłączenie komputera | NOT_RUN | NOT_RUN | NOT_RUN | Brak |
| H02 | LCD, dotyk/BOOT, fizyczny Start i ponowny setup | NOT_RUN | NOT_RUN | NOT_RUN | Brak |
| H03 | WPA2 provisioning, NVS i HTTPS | NOT_RUN | NOT_RUN | NOT_RUN | Brak |
| H04 | AOA HID bez debugowania USB | NOT_RUN | NOT_RUN | NOT_RUN | Brak |
| H05 | ADB RSA, zgoda, restart i reenumeracja | NOT_RUN | NOT_RUN | NOT_RUN | Brak |
| H06 | Screenshot PNG, rozmiar, rotacja i limity | NOT_RUN | NOT_RUN | NOT_RUN | Brak |
| H07 | Akcje, wynik shell-v2 i nowa obserwacja | NOT_RUN | NOT_RUN | NOT_RUN | Brak |
| H08 | Utrata Wi-Fi/USB, wygasła dzierżawa, brak powtórki | NOT_RUN | NOT_RUN | NOT_RUN | Brak |
| H09 | Głos, STT, opcjonalny TTS i dwa mikrofony | NOT_RUN | NOT_RUN | NOT_RUN | Brak |
| H10 | Cała pętla, audyt, trwałość i dłuższa praca | NOT_RUN | NOT_RUN | NOT_RUN | Brak |

Wariant Touch wymaga własnego wiersza/rejestru dla H02; wyniku wersji bez
dotyku nie wolno przenieść na obsługę CST816. Gdy wariant nie jest budowany
ani oferowany, oznacz go `NOT_IN_SCOPE` z uzasadnieniem; nie zmieniaj na PASS.

## Procedury i kryteria

### H01 — energia i rola USB

Przy wyłączonym telefonie/odłączonym porcie sprawdź źródło 5 V, kierunek BUS
i brak konfliktu zasilaczy. Po wgraniu firmware odłącz komputer. Sprawdź
VBUS przed i po fizycznym Start, pod obciążeniem i podczas enumeracji
telefonu. Dla Waveshare sprawdź także, że zewnętrzny dok zachowuje poprawną
ochronę niezależnie od programu. Obserwuj temperaturę i restart/brownout.

PASS: Android jest urządzeniem USB, napięcie i prąd odpowiadają kwalifikacji
konkretnego toru, nie występuje zasilanie wsteczne ani reset. Odczyt bitu
AW9523 nie jest wystarczającym dowodem. Zapisz wartości mierzone i schemat.

### H02 — obsługa fizyczna

Sprawdź orientację, kolory i czytelność całego LCD. Zweryfikuj współrzędne
każdego narożnika dotyku CoreS3/Touch i działanie BOOT Waveshare.
Start hosta musi następować dopiero po krótkim naciśnięciu/dotyku.
Przytrzymaj wejście przez 7 s i sprawdź powrót do setup po restarcie.

PASS: brak podwójnych kliknięć, fałszywych przytrzymań i przypadkowego
uruchomienia hosta; ponowny setup nie kasuje klucza ADB. Zapisz rewizję LCD.

### H03 — konfiguracja i sieć

Na pustej konfiguracji połącz się z AP wskazanym na LCD. Otwórz
`http://192.168.4.1`, wpisz kod i zapisz dane testowego gatewaya. Sprawdź
restart, połączenie z Wi-Fi i trwałość po odłączeniu zasilania. Wywołanie
konfiguracji bez poprawnego kodu ma zostać odrzucone. Sprawdź także błędny
token, certyfikat HTTPS i niedostępny serwer.

PASS: rzeczywisty gateway przyjmuje tylko właściwy token urządzenia;
nieważny certyfikat nie jest akceptowany; dane uwierzytelniające nie są
wypisywane w logach. Zmierz czas od uruchomienia do gotowości sieci.

### H04 — AOA HID przed ADB

Na odblokowanym telefonie z wyłączonym debugowaniem USB użyj Tab/Enter/Back
lub ruchu myszy w panelu KM. Zweryfikuj kliknięcie, zwolnienie przycisków
i brak zapętlonego klawisza po utracie sterowania. Sprawdź, czy status
poprawnie rozróżnia AOA ready od ADB ready.

PASS: wspierany telefon przyjmuje HID, a brak ADB nie jest przedstawiany
jako możliwość wykonywania screenshotów. W razie braku AOA zapisz
`UNSUPPORTED` dla danego telefonu; nie przechodź do sztucznego wyniku PASS.

### H05 — klucz ADB i zmiana połączenia

Właściciel telefonu ręcznie włącza debugowanie USB. Najpierw odrzuć
zaufanie RSA; następnie rozłącz i świadomie zatwierdź klucz. Zrestartuj
kontroler, sprawdź ten sam klucz, a później cofnij zaufanie na telefonie.
Sprawdź odłączenie/podłączenie, włączenie debugowania i nową enumerację.

PASS: bez zgody nie ma ADB; restart nie generuje nowej tożsamości bez powodu;
nowe połączenie tworzy nową generację/sesję; stare akcje nie przechodzą na
inny telefon. Uszkodzone NVS wymaga jawnego odzyskiwania, nie cichej wymiany RSA.

### H06 — rzeczywisty obraz telefonu

Wyświetl na telefonie znany nieprywatny wzorzec, prześlij PNG i porównaj go
z ekranem. Powtórz dla pionu/poziomu i obrazu o dużym rozmiarze. Sprawdź
odrzucenie obrazu ponad skonfigurowany limit. Osobno sprawdź ekran/aplikację,
która blokuje przechwytywanie: brak treści nie może być uznany za prawidłową
obserwację celu.

PASS: plik ma zgodne wymiary/treść i poprawny koniec PNG; nie jest uszkodzony
przez CRLF; gateway nie publikuje niekompletnego obrazu. Zapisz liczbę bajtów,
czas ADB→HTTPS i maksymalną zajętość pamięci. Limity firmware i gatewaya
muszą być świadomie dobrane do tego samego telefonu.

### H07 — ograniczone akcje i weryfikacja

Na testowym ekranie wykonaj po jednym `tap`, `swipe`, `key`, ASCII `text`,
HTTP(S) `open_url`, `wait` i `screenshot`. Zweryfikuj rzeczywistą zmianę
obrazu po kroku. Prześlij niepoprawne współrzędne, Unicode do `input text`,
tekst z `%` i polecenie spoza schematu. Sprawdź poprawny oraz błędny status
wyjścia shell-v2.

PASS: właściwa akcja działa, odrzucona nie jest wykonywana; sam koniec
strumienia bez exit 0 nie daje sukcesu. Akcja oparta o poprzedni screenshot
lub generację telefonu jest blokowana. Nie badać tego na ekranie płatności.

### H08 — awarie i niepewność

Odetnij Wi-Fi przed pobraniem akcji, po wykonaniu USB i podczas odpowiedzi
na POST wyniku. Odłącz USB w środku PNG i akcji. Zrestartuj gateway po
wydaniu dzierżawy. Zmień czas oczekiwania tak, by lease wygasła.

PASS: nie dochodzi do drugiego kliknięcia z powodu utraty potwierdzenia;
niepewny stan jest widoczny, ponowienie wyniku zachowuje ten sam klucz,
a dalsze kroki czekają na rozstrzygnięcie. Zapisz zdarzenia, identyfikatory
akcji/lease oraz nagranie testowego ekranu. Zniknięcie starej sesji z panelu
nie jest dowodem, że jej akcja nie została wykonana.

### H09 — audio

Po ADB ready uruchom nagranie krótkim naciśnięciem, wypowiedz polski cel
i sprawdź rzeczywistą transkrypcję. Sprawdź ciszę, głośne tło, opóźnione STT,
błędną odpowiedź API i opcjonalny TTS. Osobno potwierdź dwa kanały ES7210
w narzędziu diagnostycznym, pamiętając, że aplikacja do STT wybiera kanał 1.

PASS: poprawny WAV mono PCM16 16 kHz, brak zablokowania audio/pamięci,
wiarygodne komunikaty o błędach. Nie wpisuj „beamforming”, „AEC” ani
„wake-word”, ponieważ bieżąca aplikacja ich nie implementuje. Zapisz
czas nagrania, STT, planu i odtwarzania osobno.

### H10 — pętla zadania i trwałość

Wykonaj prosty, odwracalny cel, np. otwarcie publicznej strony i przewinięcie
do wskazanego nagłówka. Potwierdź kolejność zamiar → obraz → propozycja →
lease → wynik → nowy obraz. Sprawdź zatrzymanie przy limicie kroków i żądanie
zgody w panelu operatora. Uruchom serię co najmniej 30 minut i obserwuj
wolną pamięć, temperaturę, energię oraz resety.

Celowo uniemożliw eksport JSONL, zachowując zapisywalną bazę SQLite.
Sprawdź `event_export: deferred`, przywróć zapis i odtwórz projekcję.
Uruchom offline walidację faktycznego strumienia:

```sh
python scripts/standards.py check --events .state/gateway/logs/wellmanifest-events.jsonl
```

Zatrzymaj gateway i uruchom `clikvm --env-file .env rebuild-projections
--destination .state/hil-rebuilt` do nieistniejącego wcześniej katalogu.
Porównaj pięć odtworzonych tabel i zachowane PNG; oryginał ma pozostać
nienadpisany. Cache TTS oraz usunięte przez retencję media nie należą do replay.

PASS: żaden potwierdzony rekord nie ginie wskutek błędu eksportu, łańcuch
audytu jest poprawny, projekcje są odtwarzalne, limit kroków działa,
a niepewne akcje wymagają rozstrzygnięcia. CLI odbudowuje stan z dziennika
w SQLite; sam eksport audytu nie jest pełną kopią zapasową.

## Zamknięcie raportu

Do każdego PASS/FAIL/UNSUPPORTED dołącz dowód, konfigurację i obserwowany
wynik. Czasów, prądu, stabilności audio i liczby wspieranych telefonów nie
uzupełniaj wartościami szacowanymi jako pomiary. Wydanie demonstracyjne
powinno wymieniać konkretny zweryfikowany zestaw, a nie „dowolny Android”.
W razie H01 FAIL wstrzymaj dalsze testy USB do poprawy toru zasilania.
