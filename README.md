# DotaHelper & Dota Companion

Двойной набор инструментов для Dota 2:
1. **DotaHelper (`python main.py`)**: компаньон для лобби с автоматическим обнаружением 9 игроков через Dota GSI и Steamworks Coplay, БЕСПЛАТНОЙ оценкой инвентарей без сторонних платных ключей, а также Vision-переводом чата.
2. **Dota Companion (`python -m dota_companion`)**: легковесный локальный OCR-переводчик чата (Windows OCR, PaddleOCR, Tesseract, Gemini, OpenRouter, DeepSeek, Ollama) с интеграцией с Soundpad.

---

### 🛡️ Архитектура безопасности (Без инжекта и вмешательства в память)

Программа разработана с максимальной осторожностью по отношению к Valve Anti-Cheat (VAC):
- ❌ **НЕ** использует `OpenProcess`, `ReadProcessMemory` или `WriteProcessMemory`.
- ❌ **НЕ** внедряет сторонние DLL в процесс игры (`dota2.exe`).
- ❌ **НЕ** модифицирует графические пайплайны DirectX / Vulkan.
- ❌ **НЕ** использует сетевые снифферы уровня ядра (WinPcap / Npcap).
- ✅ Взаимодействие построено исключительно на штатных внешних интерфейсах:
  1. Официальный локальный HTTP-протокол Dota 2 Game State Integration (GSI).
  2. Официальный клиентский SDK Steamworks (`steam_api64.dll`, AppID 570).
  3. Стандартный захват экрана ОС Windows (MSS / GDI) и внешний полупрозрачный оверлей Qt.

> *Примечание: несмотря на отсутствие инъекций и модификаций исполняемых файлов игры, любое стороннее ПО используется на собственное усмотрение пользователя.*

---

### 📂 Архитектура репозитория

```
DotaHelper/
├── config.json                     # Конфигурация DotaHelper (валюта, порог цен, прокси, OpenRouter)
├── steam_appid.txt                 # AppID 570 (требуется для Steamworks SDK)
├── steam_api64.dll                 # Библиотека Steamworks SDK
├── main.py                         # Главная точка входа DotaHelper (поддерживает флаг --companion)
│
├── core/                           # Модули DotaHelper
│   ├── gsi_server.py               # Локальный HTTP GSI-сервер + автогенерация CFG
│   ├── steam_coplay.py             # Steamworks SDK (определение 9 игроков лобби)
│   ├── free_evaluator.py           # БЕСПЛАТНАЯ оценка инвентарей (Market Dota 2 дамп, кэш SQLite 12ч, фильтр <$0.5, авто-прокси)
│   ├── chat_translator.py          # Vision-переводчик чата через OpenRouter API (с DPI масштабированием)
│   └── soundpad.py                 # Простой контроллер воспроизведения звуков
│
├── ui/                             # Интерфейс DotaHelper
│   └── overlay.py                  # Оверлей на PyQt6 (инвентари игроков, карточки чата)
│
├── dota_companion/                 # Пакет Dota Companion (автономный переводчик + саундпад)
│   ├── __main__.py                 # Точка входа модуля (python -m dota_companion)
│   ├── app.py                      # Контроллер приложения Dota Companion
│   ├── core/                       # Настройки (атомарное сохранение), хоткеи, логгер
│   ├── ocr/                        # Мультибэкенд OCR (Windows OCR, PaddleOCR, Tesseract, slang)
│   ├── soundpad/                   # Аудиомикшер и VAC-совместимый саундпад
│   └── ui/                         # Окна калибровки, оверлеи и диалоги настроек
│
├── tests/                          # Модульные тесты
│   ├── test_free_evaluator.py      # Тесты чекера инвентарей и парсера цен
│   └── test_dota_companion.py       # Тесты настроек, сленга и валидации
│
└── requirements.txt                # Зависимости Python
```

---

### 🚀 Установка и запуск

#### 1. Подготовка окружения
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

#### 2. Запуск основного компаньона (DotaHelper)
```powershell
python main.py
```
*При первом запуске программа автоматически находит директорию Dota 2 и создает конфиг `gamestate_integration_dotahelper.cfg`.*

#### 3. Запуск OCR & Soundpad (Dota Companion)
```powershell
python -m dota_companion
# либо
python main.py --companion
```

#### Опционально: Tesseract и языковые пакеты
Для использования Tesseract OCR в `dota_companion` (если не используется нативный Windows OCR):
1. Скачайте инсталлятор [Tesseract OCR for Windows](https://github.com/UB-Mannheim/tesseract/wiki).
2. При установке выберите языки: **Russian**, **English** (китайский язык обрабатывается через нативный Windows OCR или AI).
3. Добавьте путь к `tesseract.exe` в системную переменную `PATH` или задайте `TESSERACT_PATH`.

---

### ⚙️ Настройки и возможности

#### БЕСПЛАТНЫЙ чекер инвентарей (DotaHelper)
- **Источник цен**: актуальный публичный дамп цен Market Dota 2 (`https://market.dota2.net/api/v2/prices/USD.json` или `RUB.json`).
- **Кэш цен**: `prices_cache.json` обновляется не чаще одного раза в 6 часов.
- **Кэш инвентарей**: локальная SQLite-база `inventory_cache.db` хранит инвентари игроков 12 часов.
- **Фильтрация ширпотреба**: предметы дешевле `$0.5` автоматически отфильтровываются из итоговой суммы инвентаря.
- **Обход блокировок**: поддержка SOCKS5/HTTP прокси, реверс-прокси Cloudflare Worker (`{worker_url}/inventory/{steam_id}/...`) и автоматической подгрузки живых HTTPS-прокси через Proxyscrape API.

#### Хоткеи по умолчанию

| Приложение | Хоткей | Назначение |
|---|---|---|
| **DotaHelper** | `F8` | Перевод выделенной области чата через OpenRouter Vision |
| **DotaHelper** | `Ctrl + Shift + F8` | Интерактивный выбор области чата (ROI) |
| **Dota Companion** | `F8` | Распознавание и перевод чата |
| **Dota Companion** | `Ctrl + Shift + F8` / `Ctrl + Shift + T` | Калибровка области захвата экрана |
| **Dota Companion** | `Ctrl + Shift + F10` | Безопасное завершение приложения |
