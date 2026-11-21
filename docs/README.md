---
{
  "schema": "wellmanifest.docs/document/v1",
  "id": "clikvm-documentation-index",
  "kind": "information",
  "version": 1,
  "title": "Indeks dokumentacji clikVM",
  "status": "draft",
  "owner": "clikVM/m5stack",
  "scope": "repository",
  "created": "2026-11-21",
  "updated": "2026-11-21",
  "review_after": "2026-12-21"
}
---

# Dokumentacja

Zacznij od [README projektu](../README.md), następnie wybierz instrukcję poniżej.
Status `draft` oznacza oczekującą kwalifikację fizycznego zestawu.

| Dokument | Kiedy go użyć |
|---|---|
| [System wielourządzeniowy](universal-system.md) | Wybór kanału, role, wspólny cykl i etapy wdrożenia |
| [Agent desktopowy](desktop-agent.md) | Windows, macOS, Linux X11; instalacja i rzeczywisty doctor |
| [KVM i API przez sieć](network-kvm.md) | RFB/VNC, kontrakty HTTP, ograniczenia transportu |
| [Tryby M5Stack](controller-modes.md) | Dziewięć profili i rozłączne role USB host/device/sieć |
| [Macierz możliwości JSON](../gateway/docs/compatibility.json) | Maszynowy opis implementacji, wymagań i statusu kwalifikacji |
| [Pierwsze uruchomienie](getting-started.md) | Gateway, konfiguracja modelu, kompilacja i połączenie telefonu |
| [Architektura](architecture.md) | Granice komponentów, sesje, idempotencja, CQRS i zdarzenia |
| [Sprzęt i połączenia](hardware.md) | CoreS3, Waveshare, GPIO, audio, VBUS oraz CC |
| [API gatewaya](../gateway/docs/API.md) | Implementacja klienta, nagłówki i kontrakty poleceń |
| [OpenAPI](../gateway/docs/openapi.json) | Maszynowy snapshot rzeczywistego API |
| [USB i ADB](../firmware/components/clik_usb/README.md) | Klient hosta, RSA, pakiety, ograniczenia i testy |
| [Wdrożenie i obsługa](deployment.md) | HTTPS, Docker, wolumen, backup i odtwarzanie |
| [Ograniczenia wydania](limitations.md) | Co jest zaimplementowane i co wymaga dalszej pracy |
| [Walidacja wydania](validation.md) | Wykonane testy oraz brakujące dowody |
| [Procedura HIL](hardware-validation.md) | Testy konkretnej płytki, telefonu, przewodu i zasilania |
| [Wellmanifest i Wellman](standards.md) | Schematy, rejestr wymagań, jawne luki i polecenia |
| [ADR-001: runtime-service](analysis/ADR-001-runtime-service.md) | Uzasadnienie wyboru profilu adopcji |
| [Katalog błędów](../errors/README.md) | Awarie USB, niepewne akcje i niedostępne API |

Raporty maszynowe znajdują się w `analysis/`. Schematy w `.governance/upstream`
mają przypięte źródła; nie wolno ich zmieniać bez aktualizacji pinu i dowodów.
Kontrole standardów nie zastępują kwalifikacji elektrycznej ani Android HIL.
