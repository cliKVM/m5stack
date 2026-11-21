# CLIKVM-UPSTREAM-001 — brak usługi modelu, STT lub TTS

## Rozpoznanie

Konfiguracja endpointu jest pusta, serwer odrzucił uwierzytelnienie,
przekroczono czas oczekiwania albo odpowiedź nie odpowiada wybranemu
kontraktowi. Publiczny rejestr OQLLM POA nie dowodzi obsługi audio lub vision.

## Postępowanie

Sprawdź jawne `CLIK_MODEL_ENDPOINT`, `CLIK_STT_ENDPOINT` i
`CLIK_TTS_ENDPOINT` oraz rzeczywisty kontrakt usługi. W logach przechowuj
status i identyfikator błędu bez sekretów i prywatnych treści ekranu.
Ponawiaj tylko operacje sieciowe, których semantyka na to pozwala. Awaria
modelu nie może sama wygenerować kolejnej akcji na telefonie.

## Warunek zakończenia

Usługa zwraca poprawnie walidowany wynik dla testowego żądania. Sam HTTP 200
na `/health/` nie wystarcza do potwierdzenia STT, TTS ani planowania działań.
