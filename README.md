# DotaHelper (VAC-Safe Companion)

Профессиональный компаньон для Dota 2 с оценкой инвентарей лобби (9 игроков) и Vision-переводом чата в реальном времени.

### 🛡️ Гарантия безопасности (VAC-SAFE):
Программа на 100% безопасна от VAC:
- ❌ **НЕ** использует `OpenProcess`, `ReadProcessMemory`, `WriteProcessMemory`.
- ❌ **НЕ** внедряет DLL и не хукает DirectX/Vulkan/Present графические пайплайны.
- ❌ **НЕ** использует сетевые снифферы уровня ядра (WinPcap / Raw Sockets).
- ✅ Взаимодействие осуществляется **исключительно** через:
  1. Официальный протокол Dota 2 Game State Integration (GSI).
  2. Официальный Steamworks SDK Client API (`steam_api64.dll`).
  3. Стандартный захват экрана Windows (GDI / PIL) и прозрачный оверлей (`WS_EX_LAYERED`).

---

### 📂 Архитектура проекта

```
D:/dotahelper/
├── config.json                     # Конфигурация (токены, ключи, хоткеи, координаты)
├── steam_appid.txt                 # Содержит AppID 570 (обязателен для Steamworks)
├── steam_api64.dll                 # Официальная библиотека Steamworks SDK
├── main.py                         # Главная точка входа, координация модулей
├── core/
│   ├── __init__.py
│   ├── gsi_server.py               # Локальный HTTP GSI-сервер + автоустановка CFG
│   ├── steam_coplay.py             # Steamworks SDK (определение 9 игроков лобби через дельту)
│   ├── free_evaluator.py           # БЕСПЛАТНАЯ оценка инвентарей (Market Dota 2 цены, фильтр <$1, кэш 12ч)
│   ├── lzt_evaluator.py            # Модуль обратной совместимости
│   └── chat_translator.py          # Vision-переводчик чата через OpenRouter API (F8 / Ctrl+Shift+F8)
└── ui/
    ├── __init__.py
    └── overlay.py                  # Оверлей на PyQt6 (список 9 игроков + карточки перевода)
```

---

### 🚀 Быстрый старт

1. Активируйте виртуальное окружение и установите зависимости:
```powershell
.venv\Scripts\activate
pip install -r requirements.txt
```

2. Убедитесь, что `steam_api64.dll` находится в корне `D:/dotahelper/` (уже скопирован из Dota 2).
3. Запустите DotaHelper:
```powershell
python main.py
```
*(При первом запуске программа автоматически найдет папку Dota 2 и создаст файл `gamestate_integration_dotahelper.cfg`)*

---

### ⚙️ Настройка (через интерфейс ⚙️ или `config.json`):

Нажмите иконку **⚙️** в заголовке оверлея или отредактируйте `config.json`:
- **`pricing.currency`**: Валюта расчета цен предметов (`USD` или `RUB`). База цен скачивается 100% бесплатно с Market Dota 2.
- **`pricing.min_item_price`**: Минимальная цена предмета (по умолчанию `1.0` $). Вещи дешевле порога не учитываются в сумме инвентаря.
- **`pricing.proxy`**: Опциональный HTTP/SOCKS5 прокси (например, `http://127.0.0.1:10808` или `socks5://127.0.0.1:10808`) или URL реверс-прокси Cloudflare Worker на случай блокировок Steam Community.
- **`openrouter.api_key`**: API ключ от [OpenRouter](https://openrouter.ai/) для Vision-перевода чата.
- **`openrouter.model`**: По умолчанию `google/gemini-2.5-flash` (быстрая и точная мультимодальная модель).

---

### ⌨️ Горячие клавиши

| Хоткей | Назначение |
|---|---|
| **`F8`** | Захват области внутриигрового чата и перевод через нейросеть (OpenRouter Vision) |
| **`Ctrl + Shift + F8`** | Интерактивный выбор области чата на экране (ROI selector) |
| **`⚡ Сканировать`** | Принудительное сканирование лобби и обновление инвентарей |
