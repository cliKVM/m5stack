# Sieciowy KVM oraz sterowanie przez API

## Wybór kanału

| Dostępna funkcja celu | Zalecany backend | Co widzi agent | Co wykonuje |
|---|---|---|---|
| Można uruchomić proces w sesji PC | `native` | Zrzut głównego ekranu | Klawiatura/mysz i launcher URL |
| Jest wcześniej skonfigurowany VNC/RFB | `rfb` | Framebuffer tego serwera | Zdarzenia klawiatury i myszy RFB |
| Jest stabilne API urządzenia/usługi | `http-api` | Stan JSON wybranej funkcji | Wyłącznie skonfigurowane operacje |
| Jest tylko USB/BLE HID | Obecnie kanał KM | Brak obrazu | Nie wystarcza do autonomicznej kontroli wizualnej |
| Potrzebny BIOS albo obraz bez działającego OS | Zewnętrzny sprzętowy KVM | Wymaga osobnego źródła obrazu | Poza zakresem tego node |

Wi-Fi/BLE jest transportem, a nie automatycznym źródłem obrazu. RFB daje obraz
dzięki serwerowi na urządzeniu docelowym. REST pozwala weryfikować stan tylko
w zakresie udostępnionego API. Samo przełączenie ESP32 z HID na USB host nie
tworzy kanału zrzutów PC ani nie omija wymaganej zgody Android ADB.

## RFB/VNC: realna implementacja 0.2

`clikvm_node/rfb.py` implementuje RFB 3.3, 3.7 i 3.8. Używa formatu pikseli
32-bit true-color, kodowania Raw i pseudo-kodowania DesktopSize. Przyjmuje
fragmentowane odczyty TCP, sprawdza granice prostokątów i wymaga pełnego
odświeżenia pikseli przy każdej obserwacji, także po zmianie rozdzielczości. PNG
wykorzystuje ten sam mechanizm klatek i dzierżaw co pozostałe kanały.

Obsługiwane wejście to kliknięcie lewym przyciskiem, przeciągnięcie, określone
klawisze i tekst ASCII. Wariant RFB nie reklamuje `open_url`: protokół nie ma
przenośnego launchera URL. Nie udostępnia schowka, audio, Tight/ZRLE, negocjacji
VeNCrypt, logowania domenowego ani własnościowego uwierzytelniania Apple Remote
Desktop. Serwer musi pozwalać na zgodny profil RFB; nazwa „VNC” sama w sobie nie
potwierdza zgodności.

Podstawą implementacji jest
[RFC 6143](https://www.rfc-editor.org/rfc/rfc6143.html). Protokół i jego metody
uwierzytelniania opisują możliwości transportu, a nie gwarantują dostępu do
każdego pulpitu czy chronionych treści.

### Tunel do już autoryzowanego serwera

Przykład zakłada własny serwer VNC nasłuchujący na pętli zwrotnej komputera
docelowego. Utwórz wcześniej zaufane połączenie SSH:

```sh
ssh -N -L 5901:127.0.0.1:5900 user@owned-host
```

Na komputerze z tunelem:

```sh
python3 -m venv .venv-node
.venv-node/bin/python -m pip install -e 'agents/desktop[rfb]'
cp agents/desktop/examples/rfb.json node.json
cp agents/desktop/examples/node.env.example node.env
chmod 600 node.env
```

Uzupełnij adres gateway, identyfikator celu, `CLIK_NODE_TOKEN` i
`CLIK_RFB_PASSWORD`. W przykładzie `host=127.0.0.1`, `port=5901`,
`tls=false`, `allow_plaintext=true`, `allow_none_auth=false`.
Jawny tryb plaintext dotyczy odcinka do lokalnego końca tunelu; poufność drogi
do zdalnego urządzenia zapewnia skonfigurowane SSH, a nie stare hasło VNC.

```sh
.venv-node/bin/clikvm-node --config node.json --env-file node.env doctor
.venv-node/bin/clikvm-node --config node.json --env-file node.env run
```

RFB uwierzytelnianie VNC używa kompatybilnego DES challenge-response z pierwszymi
ośmioma bajtami hasła; nie szyfruje całej sesji. DES jest mechanizmem legacy,
co zaznacza [dokumentacja PyCryptodome](https://www.pycryptodome.org/src/cipher/des).
Brak hasła wymaga dodatkowego `allow_none_auth=true`. Wersja bez szyfrowanego
tunelu nie powinna być przedstawiana użytkownikowi jako bezpieczne połączenie.

### Zewnętrzna otoczka TLS

Alternatywnie połącz się z końcówką TLS, która po udanym handshake przekazuje
strumień RFB, np. świadomie skonfigurowanym proxy TLS przed własnym VNC:

```json
{
  "host": "vnc.owned.example",
  "port": 5901,
  "tls": true,
  "server_name": "vnc.owned.example",
  "ca_file": "/path/to/private-ca.pem",
  "password_env": "CLIK_RFB_PASSWORD",
  "allow_none_auth": false,
  "platform": "linux"
}
```

To zawartość pola `options` pełnego `node.json`. Certyfikat i nazwa hosta są
weryfikowane. `ca_file` jest opcjonalne, gdy CA należy do systemowego magazynu.
Jest to TLS **przed** RFB, odrębny od VeNCrypt negocjowanego wewnątrz protokołu.
Nie kieruj tej konfiguracji do zwykłego portu VNC i nie oczekuj automatycznej
konwersji zabezpieczeń serwera.

Testy obejmują rzeczywiste połączenia TCP do deterministycznego serwera RFB,
fragmentację, wersje 3.3/3.7/3.8, challenge-response, zmianę rozmiaru, zły
prostokąt, przyciski i klawisze. Użycie konkretnego serwera, certyfikatu TLS,
tunelu SSH i fizycznego pulpitu wymaga osobnego odbioru; te scenariusze nie
są oznaczone jako wykonane.

Po błędzie wysyłania wejścia node próbuje zwolnić przycisk i wszystkie
potencjalnie wciśnięte klawisze, a następnie zamyka niepewny transport.
Nie zakłada, że timeout oznacza brak dostarczenia zdarzenia. Wynik pozostaje
`uncertain`; dostarczenie zwolnienia po utracie połączenia nie jest gwarantowane
i może wymagać lokalnej interwencji użytkownika.

## HTTP API: stan i zamknięty katalog operacji

Backend `http-api` wymaga jednego ustalonego `base_url`, ścieżki GET
`state_path` i katalogu do 12 operacji. Model dostaje tylko identyfikator,
opis, schemat argumentów i flagę potwierdzenia. Adres, tokeny i nagłówki
pozostają w lokalnej konfiguracji node. Nie ma akcji „wywołaj dowolny URL”.

`examples/http-api.json` opisuje światło Home Assistant:

- obserwacja: `GET /api/states/light.office`;
- włączenie: `POST /api/services/light/turn_on` z ustaloną encją i jasnością
  1–255;
- wyłączenie: `POST /api/services/light/turn_off` z ustaloną encją;
- uwierzytelnianie: lokalny sekret
  `CLIK_DEVICE_API_AUTHORIZATION="Bearer ..."`.

Endpointy i format Bearer wynikają z
[oficjalnego REST API Home Assistant](https://developers.home-assistant.io/docs/api/rest/).
Przykład wymaga własnej encji, danych dostępu i testu na instalacji klienta.
Nie ustanawia automatycznie konta ani uprawnień w Home Assistant.

Szablon JSON `{"$arg":"brightness"}` wstawia jeden argument po walidacji.
`{"$arguments":true}` wstawia cały zwalidowany obiekt. Pozostałe elementy
pozostają stałe. Argumenty nie zmieniają adresu, metody, ścieżki ani nagłówków.
Każda operacja ma jawny JSON Schema `type: object` i
`additionalProperties: false`; referencje `$ref`, `$dynamicRef` i
`$recursiveRef` są odrzucane. Limit schematu wynosi 8192 bajty, a zagnieżdżenia
JSON 16 poziomów.

Stan musi być obiektem JSON o rozmiarze do 60000 bajtów, bez NaN/Infinity i
najwyżej 16 poziomów zagnieżdżenia. Jest wysyłany jako
`POST /v1/sessions/{sid}/observations` z `frame_id` i `state`. Nie trafia do
endpointu PNG. Brama przekazuje ten stan modelowi jako dane, a katalog API
określa dopuszczalne działania. W razie zbyt dużego obiektu wybierz węższy
endpoint albo własny adapter zwracający stan konkretnej funkcji.

Po sukcesie HTTP node odczytuje nowy stan. Dopiero ten stan pozwala ocenić
wynik celu. Przykładowo `200 OK` dla polecenia światła nie gwarantuje, że
fizyczne światło jest już włączone. Jeśli API ma opóźnione wykonanie, planner
może użyć ograniczonego `wait` i następnej obserwacji. Po timeout mutacji
wynik jest `uncertain`; node nie ponawia jej automatycznie.

### Ograniczenie dostępu sieciowego

HTTPS i weryfikacja certyfikatu są domyślne. Jawny `allow_http=true` dopuszcza
lokalny test albo prywatny adres IP. Takie HTTP nie szyfruje tokenu API.
Przekierowania są zawsze odrzucane; proxy z otoczenia procesu nie jest
używane. Ścieżki są ustalane w konfiguracji, względne wobec jednego origin.
Pola wybrane przez model dotyczą tylko ciała JSON zgodnego ze schematem.

Ten katalog ogranicza możliwości modelu do wcześniej wybranej usługi. Nie
jest sieciowym firewallem ani izolacją złośliwie skonfigurowanego API: osoba
wdrażająca nadal odpowiada za zaufanie do DNS, serwera i semantyki operacji.
Nie dodawaj ogólnego `execute`, shella, URL callback ani operacji pobierania
nieograniczonych URL do katalogu tylko po to, aby ominąć te ograniczenia.
Sekrety i odpowiedzi zawierające dane prywatne nie są wypisywane do logów node.

## Kontener i autostart

`agents/desktop/Dockerfile` i `compose.yaml` dostarczają kontener dla RFB/API.
Proces ma UID 10001, trwały katalog `/state`, system plików tylko do odczytu
poza wolumenem i `/tmp`, a capabilities Linuksa są usunięte. Przygotuj w
`agents/desktop` prywatny `.env.node` i `node.json`, w którym `state_dir` ma
wartość `/state`. Następnie z katalogu głównego repozytorium:

```sh
docker compose -f agents/desktop/compose.yaml build
docker compose -f agents/desktop/compose.yaml up -d
docker compose -f agents/desktop/compose.yaml logs --tail 50
```

Ustaw adresy osiągalne z sieci kontenera. `127.0.0.1` w kontenerze oznacza
kontener, więc nie jest adresem tunelu uruchomionego na jego hoście. Przy
tunelu lokalnym najprostsze wdrożenie to uruchomienie node bezpośrednio na
tym samym hoście; inne topologie wymagają świadomej konfiguracji sieci i
uwierzytelnionego kanału. Nie uruchamiaj natywnego desktopu w tym kontenerze
i nie montuj socketu GUI, aby udawać przenośne wsparcie OS.

Konfiguracja Docker jest dostarczona jako przykład wdrożeniowy. Build i
uruchomienie Docker, produkcyjne API, rzeczywisty VNC, fizyczne PC oraz
końcowy test M5Stack → OQLLM → urządzenie nie zostały tu wykonane. Raport
`agents/desktop/verification-report.json` oddziela te pozycje od wykonanych
testów protokołu i integracji z gateway.
