# M5Stack jako zdalny kontroler heyasa — 0.2.0

Tryb `remote` używa mikrofonu, głośnika i ekranu M5Stack jako interfejsu użytkownika. Obraz i wejście pochodzą z **aktywnego connectora urządzenia docelowego**, a nie z USB płytki. PC może używać `native-agent`, zgodny serwer VNC — `rfb`, a urządzenie sieciowe — `http-api`. Działający Android ADB nadal może być bezpośrednio obsługiwany przez firmware w odrębnym trybie.

## Przygotowanie pierwszej pary

W katalogu repozytorium, po instalacji gatewaya:

```sh
clikvm init-env --output .env --device pc-01 --controller m5-01
```

Polecenie tworzy plik z uprawnieniami `0600`, trzema różnymi losowymi tokenami i przypisaniem `m5-01` → `pc-01`. Nie wypisuje sekretów i odmawia nadpisania istniejącego pliku. Token urządzenia dostaje connector PC, token kontrolera — M5, a token operatora — panel przeglądarkowy. **Token operatora nie trafia na płytkę.**

Odpowiednie ustawienia serwera, z opisowymi wartościami zamiast rzeczywistych sekretów:

```dotenv
CLIK_DEVICE_TOKENS_JSON='{"pc-01":"DISTINCT_RANDOM_TARGET_TOKEN_AT_LEAST_24_CHARS"}'
CLIK_OPERATOR_TOKEN='DISTINCT_RANDOM_OPERATOR_TOKEN_AT_LEAST_24_CHARS'
CLIK_CONTROLLER_TOKENS_JSON='{"m5-01":"DISTINCT_RANDOM_CONTROLLER_TOKEN_AT_LEAST_24_CHARS"}'
CLIK_CONTROLLER_TARGETS_JSON='{"m5-01":"pc-01"}'
CLIK_TARGET_ONLINE_SECONDS=30
```

Istniejącego `.env` nie regeneruj. Dodaj nowe losowe tokeny i mapowania do konfiguracji operatora, zachowując już przypisane urządzenia. Serwer przy starcie sprawdza, że każdy kontroler ma dokładnie jeden istniejący target, a wszystkie tokeny są różne. Zmiana przypisania wymaga zmiany konfiguracji i restartu gatewaya. Kontroler nie może sam wybierać urządzenia ani eskalować uprawnień.

Uruchom gateway i skonfiguruj connector zgodnie z [instrukcją desktop/node](../../agents/desktop/README.md). W trybie remote firmware używa pól konfiguracji `device_id`/`device_token` jako **controller ID/token**; identyfikator targetu pozostaje po stronie gatewaya. Wybór trybu i konkretnego profilu płytki opisuje dokumentacja firmware. Adres gatewaya jest rzeczywistym adresem własnego wdrożenia z poprawnym TLS, a nie automatycznie `api.oqllm.com`.

## Mały kontrakt HTTP

Wszystkie ścieżki poniżej wymagają `Authorization: Bearer <controller-token>`, pasującego do `{id}`. Każdy POST wymaga nowego `Idempotency-Key` dla nowego logicznego żądania; dokładne ponowienie zachowuje klucz i treść. Token kontrolera jest odrzucany przez zwykłe endpointy urządzeń i operatora.

| Metoda i ścieżka | Treść | Wynik |
| --- | --- | --- |
| `GET /v1/controllers/{id}/target` | brak | Przypisany target, bieżąca sesja, stan, dostępność i możliwości |
| `POST /v1/controllers/{id}/intent` | `{"text":"…","expected_session_id":"…"}` | `{"intent_id":"…","status":"planning"}`; najpierw wymusza nową obserwację |
| `POST /v1/controllers/{id}/capture` | `{"expected_session_id":"…"}` | `{"capture_requested":true}` |
| `POST /v1/controllers/{id}/cancel` | `{"expected_session_id":"…"}` | `{"status":"cancelled"}` |
| `POST /v1/controllers/{id}/audio/stt` | surowy WAV, `Content-Type: audio/wav` | `{"text":"…"}` |
| `POST /v1/controllers/{id}/audio/tts` | `{"text":"…"}` | WAV PCM16 mono 16 kHz |

Przykładowy odczyt targetu:

```json
{
  "controller_id":"m5-01",
  "target_device_id":"pc-01",
  "session_id":"a-session-uuid",
  "ready":true,
  "state":"idle",
  "message":"Gotowy na polecenie",
  "latest_frame":12,
  "platform":"linux",
  "transport":"native-agent",
  "observation_kind":"screenshot",
  "capabilities":["screenshot","keyboard","mouse","open_url","native_agent"],
  "action_types":["tap","swipe","key","text","open_url","wait","screenshot"]
}
```

`ready` oznacza aktywny kanał transportu, niedawny kontakt connectora i co najmniej jedną rzeczywistą obserwację. Nie oznacza certyfikowanej zgodności sprzętu ani tego, że nie ma aktywnego lub niepewnego kroku. Wartości `state` należy wyświetlać. Przed pierwszą obserwacją jest `awaiting_frame`; po utracie kontaktu `offline`; dla samego KM `observation_required`. Domyślny limit kontaktu to 30 sekund, konfigurowalny w zakresie 5–300. Gateway po restarcie czeka na ponowny kontakt urządzenia.

### Sekwencja polecenia głosowego

1. Pobierz `/target`, sprawdź `ready`, zapamiętaj `session_id`.
2. Nagraj PTT i wyślij WAV do `/audio/stt`. STT nie uruchamia żadnej akcji.
3. Wyślij uzyskany tekst do `/intent` z **zapamiętanym** `expected_session_id`.
4. Gateway żąda nowej obserwacji od connectora, nawet jeżeli poprzednia jest młodsza niż pięć sekund. Dopiero ta obserwacja trafia do planera.
5. Connector pobiera ograniczoną dzierżawę, wykonuje raz, potwierdza i przesyła kolejny obraz lub stan. Kontroler może odczytywać stan i odtworzyć opcjonalny TTS.

`expected_session_id` jest opcjonalne dla prostych klientów, lecz firmware powinien je zawsze wysyłać przy intent/capture/cancel. Chroni przed sytuacją, w której użytkownik nagrywa polecenie dla starej sesji, a podczas wolnego STT target się rozłącza lub restartuje. Taka próba daje HTTP 409 i `X-Clik-Error-Code: target_changed`; nie wolno automatycznie podmieniać ID i ponawiać starego celu.

Pozostałe kody 409: `target_unavailable` — brak aktywnego/niedawno widzianego targetu; `observation_required` — brak pierwszej obserwacji lub sam kanał KM. Błąd STT/planera nie uruchamia zastępczego automatu.

### Pauza i niepewność

`cancel` blokuje następne kroki starego zamiaru. Akcja już wydana wykonawcy otrzymuje stan `uncertain`, ponieważ mogła dotrzeć do urządzenia. Nie można jej cofnąć komunikatem HTTP. Ponowne Start jedynie pozwala przyjmować nowe cele; nie wznawia starego.

Kontroler nie zatwierdza operacji wymagających decyzji operatora i nie uzgadnia niepewnych rezultatów. Operator widzi nowy obraz lub rzeczywisty JSON, sprawdza efekt i kończy poprzednią próbę w panelu. Nowy cel jest osobną intencją. Rejestr poleceń rozdziela aktorów `controller:<id>`, `device:<id>` i `operator`, zachowując granice idempotencji.

## Usługi mowy i planowania

`CLIK_STT_ENDPOINT`, `CLIK_TTS_ENDPOINT`, ich modele, głos i klucze należy ustawić zgodnie z rzeczywistym wdrożeniem. Nie dopisujemy niepotwierdzonych ścieżek do domeny OQLLM. STT przyjmuje nagranie WAV; TTS gateway normalizuje do PCM16 mono 16 kHz. Polski tekst polecenia i STT jest zachowany. Typowana akcja `text` w 0.2.0 nadal obsługuje tylko drukowalne ASCII bez `%` na wszystkich adapterach — zapis Unicode do aplikacji wymaga osobnego przyszłego mechanizmu.

Testy tego kontraktu i dostawców wykorzystują jawne mocki HTTP. Rzeczywisty M5, mikrofony, opóźnienia Wi-Fi i usług OQLLM wymagają HIL; status `NOT_RUN` nie oznacza zakończonej integracji sprzętowej.
