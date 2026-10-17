# Wytyczne dotyczące wnoszenia wkładu (Contributing Guidelines)

Dziękujemy za zainteresowanie rozwojem projektu **`nodavia-hub`**! Praca społeczności jest kluczowa dla utrzymania wysokiej jakości oprogramowania.

---

## 1. Kodeks Postępowania (Code of Conduct)

Oczekujemy, że wszyscy uczestnicy projektu zachowają szacunek, profesjonalizm i otwartość na dyskusję merytoryczną.

---

## 2. Jak zgłosić błąd lub propozycję zmiany?

* **Zgłaszanie błędów (Issues):** Przed utworzeniem nowego zgłoszenia sprawdź, czy dany problem nie został już opisany. Opisz problem dokładnie, podając wersję środowiska, kroki do odtworzenia oraz logi błędu.
* **Propozycje funkcji (Feature Requests):** Przed rozpoczęciem prac nad dużą funkcjonalnością utwórz zgłoszenie w celu omówienia architektury z zespołem utrzymującym.

---

## 3. Proces Tworzenia Kodu i Pull Request (PR)

1. **Sforkuj repozytorium** i utwórz nowy branch dla swojej zmiany:
   ```bash
   git checkout -b feature/nazwa-funkcjonalnosci
   # lub
   git checkout -b fix/opis-poprawki
   ```

2. **Jakość kodu i testy:**
   * Przestrzegaj standardów PEP 8.
   * Uruchom linter i formatowanie kodu (ruff / black / flake8):
     ```bash
     black .
     ruff check .
     ```
   * Upewnij się, że wszystkie testy jednostkowe i integracyjne przechodzą pomyślnie:
     ```bash
     pytest
     ```

3. **Tworzenie Pull Requesta:**
   * Opisz w PR zakres wprowadzonych zmian i podaj odnośnik do powiązanego Issue.
   * Upewnij się, że każdy nowy plik źródłowy zawiera wymagany nagłówek licencyjny MPL 2.0.

---

## 4. Zasady Licencyjne (Licensing & Developer Certificate of Origin)

Projekt **`nodavia-hub`** objęty jest licencją **Mozilla Public License 2.0 (MPL 2.0)**.

Przekazując swój wkład (Pull Request), oświadczasz i zgadzasz się, że:
1. **Zgoda na licencję MPL 2.0:** Całość przekazywanego kodu staje się dostępna na warunkach licencji MPL 2.0.
2. **Prawa autorskie (DCO):** Posiadasz pełne prawa autorskie do przekazywanego kodu lub dysponujesz upoważnieniem do jego udostępnienia na licencji MPL 2.0.
3. **Prawa do poprawek:** Otrzymane poprawki do kodu `nodavia-hub` będą publicznie dostępne dla całej społeczności użytkowników i wdrożeniowców.
