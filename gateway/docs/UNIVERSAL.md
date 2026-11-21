# Profile urządzeń i obserwacje — 0.2.0

Gateway obsługuje jedną transakcyjną pętlę `session → observation → intent → action lease → result → observation`, używaną przez Android ADB, lokalny connector desktop, RFB/VNC i jawnie skonfigurowane API. Profil opisuje rzeczywisty adapter, platformę i dostępne możliwości. Nie ma mechanizmu „przejmij dowolne urządzenie”. Warunki zgodności oraz osobny status sprzętowy są w [compatibility.json](compatibility.json).

## Trzy różne rodzaje dostępu

| `observation_kind` | Źródło | Dozwolona autonomia |
| --- | --- | --- |
| `screenshot` | ADB, native desktop lub framebuffer RFB | Typowane wejście i kontrola kolejnego obrazu |
| `json-state` | Odczyt stanu z jawnie skonfigurowanego API | Wyłącznie operacje API z katalogu i oczekiwanie; bez pikseli i wirtualnego obrazu |
| `none` | Samo KM, np. HID | Brak autonomicznego zamiaru w gatewayu; potrzebny rzeczywisty kanał obserwacji |

Nie renderujemy JSON jako sztucznego screena. Panel pokazuje stan JSON w elemencie tekstowym, a model otrzymuje dane JSON bez `image_url`. Samo BLE, Wi-Fi ani USB HID nie umożliwia odczytu obrazu telefonu. Kamera jest opcjonalną przyszłą drogą obserwacji, a nie warunkiem ADB/native/RFB.

## Rozszerzenie rozpoczęcia sesji

Endpoint `/v1/devices/{device_id}/sessions` i token urządzenia pozostają bez zmian. Profil jest niezmienny w obrębie sesji. Brak `profile` zachowuje zachowanie Androida z 0.1.0: wymaga `adb` i siedmiu istniejących operacji. Pozostałe adaptery przekazują profil jawnie:

```json
{
  "boot_id":"a-new-random-id",
  "capabilities":["screenshot","keyboard","mouse","open_url","native_agent"],
  "simulated":false,
  "profile":{
    "platform":"linux",
    "os_version":"detected-by-connector",
    "label":"Komputer demonstracyjny",
    "transport":"native-agent",
    "observation_kind":"screenshot",
    "action_types":["tap","swipe","key","text","open_url","screenshot","wait"],
    "keys":["HOME","BACK","ENTER","TAB","ESCAPE","DPAD_UP","DPAD_DOWN","DPAD_LEFT","DPAD_RIGHT","DEL","APP_SWITCH"],
    "api_operations":[]
  }
}
```

Platformy schematu: `android`, `windows`, `macos`, `linux`, `ios`, `ipados`, `network`, `unknown`. Obecność wartości w schemacie **nie tworzy adaptera**; pełny KVM iOS/iPadOS, natywny RDP oraz Wayland portal nie są zaimplementowane. Transporty runtime: `android-adb`, `native-agent`, `rfb`, `http-api`, `hid-bridge`.

Connector sprawdza dostępność swoich interfejsów przed rejestracją, a potem przesyła pierwszą obserwację. Gateway sprawdza spójność deklaracji i blokuje operacje spoza `action_types`; nie wykonuje niezależnej certyfikacji systemu. Dla nie-ADB `tap/swipe` wymagają `mouse`, `key/text` — `keyboard`, `open_url` — `open_url`, `api` — `api_operation`. Obraz wymaga `screenshot`, JSON — `api_state`. Zmiana geometrii, uprawnień lub kanału wymaga ponownego ustalenia gotowości i nowej sesji.

Maksymalnie: 32 nazwy capabilities, osiem typów akcji, 11 klawiszy, 12 operacji API. Body startu sesji jest ograniczone do 128 KiB. To kontrakt konkretnego adaptera; wpis `windows` nie pozwala interpretować `HOME` jako Android launchera.

### Semantyka klawiszy

| Klawisz | Android ADB | Native desktop |
| --- | --- | --- |
| `HOME` | Launcher | Home kursora |
| `BACK` | Android Back | Alt+Left; macOS Cmd+[ |
| `APP_SWITCH` | Android aplikacje ostatnie | Alt+Tab; macOS Cmd+Tab |
| `DEL` | Android delete/backspace | Backspace |

RFB wysyła desktopowe keysyms (Home kursora / nawigacja aplikacji), nie kody
Android ADB. Connector odrzuca deklarację RFB `platform:android`, ponieważ
nie ma zweryfikowanego mapowania na nawigację systemową Androida. Backend nie deklaruje `open_url`, jeśli nie ma jawnie zaimplementowanego sposobu otwierania przeglądarki. Tekst akcji pozostaje ASCII bez `%`; głos/tekst celu może być Unicode.

## Urządzenia API

Konfiguracja connectora zawiera stały adres, ścieżkę odczytu stanu, listę metod/ścieżek oraz referencje do sekretów. Do gatewaya i modelu trafia wyłącznie ID, opis, JSON Schema argumentów i wymaganie potwierdzenia:

```json
{
  "platform":"network",
  "transport":"http-api",
  "observation_kind":"json-state",
  "action_types":["api","wait"],
  "keys":[],
  "api_operations":[{
    "id":"set_temperature",
    "description":"Ustaw temperaturę zadaną w demonstracyjnym termostacie",
    "parameters_schema":{
      "type":"object",
      "properties":{"temperature":{"type":"number","minimum":10,"maximum":30}},
      "required":["temperature"],
      "additionalProperties":false
    },
    "requires_confirmation":true
  }]
}
```

Schemat musi być poprawnym inline JSON Schema 2020-12, z `type:object` i `additionalProperties:false`, do 8192 bajtów i 16 poziomów. `$ref`, `$dynamicRef` i `$recursiveRef` są odrzucane; walidacja nie pobiera schematów z sieci. Identyfikatory operacji muszą być unikatowe. Każda akcja jest ponownie sprawdzana lokalnie przed wykonaniem.

Polecenie modelu:

```json
{"type":"api","operation":"set_temperature","arguments":{"temperature":21.5}}
```

`arguments` to JSON object do 8192 bajtów i 16 poziomów; walidator sprawdza wymagane pola, typy, zakresy i zakaz pól dodatkowych zgodnie z katalogiem. Wymagania `requires_confirmation:true` z profilu nie można wyłączyć odpowiedzią modelu. Wartości argumentów trafiają wyłącznie do skonfigurowanego body. Nie mogą zmienić celu HTTP, ścieżki, metody, nagłówków ani wybrać shella. Redirecty są odrzucane przez connector. Dostęp do zewnętrznego systemu wynika z konfiguracji właściciela.

### Przesyłanie stanu

```http
POST /v1/sessions/{sid}/observations
Authorization: Bearer <device-token>
Idempotency-Key: <random-key>
Content-Type: application/json
```

```json
{"frame_id":1,"state":{"temperature":20.5,"setpoint":21.5,"mode":"heat"}}
```

```json
{"frame_id":1,"width":0,"height":0,"resource_urn":"urn:clikvm:state:sha256:<digest>","planning_intent":null}
```

Numer `frame_id` jest wspólnym monotonicznym licznikiem **obserwacji**, nawet gdy nie ma obrazu. Zero wymiarów sygnalizuje brak pikseli. Sam stan po kanonicznym kodowaniu UTF-8 ma maksymalnie 60000 bajtów i 16 poziomów; całe body HTTP — 65536. Odczyt: `GET /v1/sessions/{sid}/observations/{frame_id}` zwraca `{frame_id,state,resource_urn}`. Próba odczytu JSON przez `/frames/{id}` lub obrazu przez `/observations/{id}` jest odrzucana kodem 415.

JSON podlega tej samej retencji i globalnej kwocie mediów co PNG: domyślnie osiem obserwacji/sesję. Dziennik domenowy i audyt zapisują hash stanu; plik JSON jest osobnym chronionym zasobem. Nie wysyłaj sekretów w obserwacji API — jest ona wejściem modelu.

## Wspólna granica wykonania

`/intent`, `/actions/next`, `/actions/{aid}/result`, potwierdzenie i uzgodnienie pozostają tymi samymi operacjami. `after_action` to `screenshot` dla obrazu lub `observation` dla JSON. `capture_requested` oznacza odświeżenie właściwego rodzaju obserwacji. Odpowiedź wyniku `next:upload_frame` jest zachowaną nazwą legacy; API-only przesyła wtedy `/observations`.

URI starych działań Androida pozostają `proc://clikvm/android/{type}/v1`. Pozostałe adaptery używają `proc://clikvm/device/{type}/v1`; API ma `proc://clikvm/device/api/v1`. Model dostaje profil targetu i historię właściwej sesji, a proponowane możliwości są sprawdzane przed wydaniem dzierżawy.

Wydanie dzierżawy nie jest zezwoleniem na powtarzanie efektu. Connector zapisuje rozpoczęcie kroku przed inputem, wysyła wynik pod stałym kluczem i po utracie potwierdzenia/restartach przechodzi do niepewności. Odbiór ACK nie potwierdza ukończenia celu; potrzebna jest nowa obserwacja. Jeżeli wyniku nie można ustalić, operator sprawdza świeży stan i zaczyna nowy cel. Przerwanie nie cofa już wysłanego inputu lub mutacji API.

## Migracja 0.1 → 0.2

Gateway dodaje do istniejących tabel `sessions.profile` (domyślnie `{}` — legacy Android) i `frames.kind` (domyślnie `png`). Zachowuje dane, tokeny, klucze idempotencji i nazwy endpointów. Triggery projekcji są odtwarzane dla nowych kolumn. Replay starszego dziennika jawnie uzupełnia tylko te brakujące pola; nie ignoruje pozostałych różnic schematu. Zachowane media JSON i PNG są kopiowane przy odtwarzaniu do nowego katalogu. Cache TTS nadal nie należy do projekcji domenowych.

Stop gateway, zachowaj kopię katalogu stanu i uruchom nową wersję w jednym procesie. Aktualizacja nie przywraca automatycznie starego zadania ani nie odsyła niepotwierdzonej akcji. Testy migracji obejmują starą postać tabel oraz stare zdarzenia projekcji. Dowody: [VERIFICATION.md](VERIFICATION.md) i [verification-report.json](verification-report.json).
