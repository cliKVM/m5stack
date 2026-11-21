# CLIKVM-USB-001 — brak właściwego połączenia Androida

## Rozpoznanie

Brak enumeracji wskazuje na tor danych, rolę USB, VBUS albo kabel. Obecność
AOA bez ADB nie oznacza awarii: telefon może mieć wyłączone debugowanie.
Stan nieautoryzowany wymaga zgody właściciela telefonu na jego ekranie.

## Postępowanie

Sprawdź profil płytki i dokumentację zasilania. Użyj kabla z liniami danych.
Potwierdź, że telefon jest urządzeniem USB, a kontroler hostem. Odblokuj
telefon, włącz debugowanie zgodnie z instrukcją i zaakceptuj właściwy klucz.
Po zmianie funkcji USB firmware powinien odtworzyć połączenie. Przy testach
zapisz model Androida, wersję OS i profil płytki, bez kluczy prywatnych.

## Warunek zakończenia

Poprawny screenshot przez autoryzowany ADB. Samo wysłanie raportu HID ani
znalezienie deskryptora nie dowodzi zakończenia konfiguracji.
