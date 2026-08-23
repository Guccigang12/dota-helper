# Dota Companion

Внешний компаньон для Dota 2 (External Companion Tool):

1. **Аналитика матча** — ростер 10 игроков с разделением на Radiant/Dire,
   оценка стоимости Steam-инвентаря через **Lolzteam Market API**.
2. **Экранный переводчик чата** — захват области чата, OCR
   (PaddleOCR / Tesseract), машинный перевод на русский, история,
   прозрачный HUD поверх игры, уведомления.
3. **Саундпад** — плавающий Always-on-Top виджет с аудио-триггерами,
   воспроизведение в выбранное устройство (Virtual Audio Cable).

Стек: **Python 3.10+ · PyQt6 · asyncio (aiohttp) · mss · sounddevice**.

---

## Безопасность (VAC)

Архитектура намеренно **не взаимодействует с процессом игры**:

- **Данные матча** — официальный *Game State Integration* (Valve, тот же
  механизм, что у Overwolf) + чтение файла `console.log` (запуск Dota 2
  с параметром `-condebug`). Никакого чтения памяти, инжекта, DLL или хуков.
- **OCR** — снимок области экрана (как OBS/Discord).
- **Саундпад** — обычный вывод звука в аудиоустройство Windows.
- **Хоткеи** — пассивные `RegisterHotKey` Win32.

Честная оговорка: абсолютную гарантию «VAC никогда не сработает» не даст
никто, но данный класс инструментов (GSI + скриншот + файл лога) не даёт
Valve повода для детекта и легален — он не читает память и не модифицирует
игру. Не добавляй в проект модули чтения памяти или инжекта.

---

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

### OCR (выбери один вариант)

**Вариант A — PaddleOCR** (лучший для китайского/английского/испанского):
```bash
pip install paddlepaddle paddleocr
```

**Вариант B — Tesseract** (fallback): установи
[Tesseract 5](https://github.com/UB-Mannheim/tesseract/wiki) и языки
`chi_sim`, `eng`, `spa`, затем:
```bash
pip install pytesseract
```
Приложение автоматически переключится на Tesseract, если PaddleOCR не
установлен.

---

## Настройка Dota 2

1. **GSI**: скопируй `config/gamestate_integration_dota_companion.cfg` в
   ```
   <Steam>/steamapps/common/dota 2 beta/game/dota/cfg/gamestate_integration/
   ```
   и перезапусти Dota 2.

2. **console.log**: в Steam → Dota 2 → Свойства → Параметры запуска добавь:
   ```
   -condebug
   ```
   (нужен для лога чата; ростер матча приходит из OpenDota по match_id).

3. **Режим окна**: для OCR переведи игру в **«Окно без рамок»**
   (Borderless Windowed) — полноэкранный режим скрывает сцену от захвата.

4. **API-токен Lolzteam Market**: возьми на
   [lzt.market/account/api](https://lzt.market/account/api) и вставь в
   поле настроек (или в файл `%APPDATA%/DotaCompanion/settings.json`,
   ключ `market_token`).

---

## Запуск

```bash
python main.py
```

Хоткеи:

| Клавиша | Действие |
|---|---|
| `Ctrl + Shift + F8` | выбор области чата |
| `F9` | пауза / возобновление OCR |
| `F10` | выход из приложения |

### Работа с модулями

- **Матч**: ростер всех 10 игроков (Steam ID, ники, герои, команды)
  приходит из GSI **в реальном времени** (`player.team2/team3`) — оценки
  считаются прямо во время игры. Steam ID появляются с фазы
  Strategy Time (в рейтинговых матчах до неё профили скрыты).
  После конца матча ростер дополнительно уточняется из OpenDota по
  `match_id`. **Оценка инвентаря автоматическая**: у каждого игрока
  известен Steam ID, и стоимость всего его Steam-инвентаря берётся
  напрямую из Steam-профиля через `GET /steam-value?link=…`. Включи
  «Авто-оценку» — все 10 игроков оценятся сами.
- **Чат**: выбери область чата, включи OCR. Распознанный текст
  переводится (Google бесплатно / **Gemini API** / DeepL / LibreTranslate)
  и попадает в историю, HUD поверх игры и уведомления. Для Gemini
  укажи API-ключ (aistudio.google.com/apikey) и модель — по умолчанию
  `gemini-3.5-flash-lite` (самая быстрая, ~0.5–0.8 с на сообщение).
  Слайдеры *Text size* и *Showtime* управляют HUD.
- **Саундпад**: положи звуки в папку `%LOCALAPPDATA%/DotaCompanion/sounds`
  (wav/mp3/ogg/flac); файл `<label>.wav` подхватится к триггеру с тем же
  именем. В «Устройство вывода» выбери виртуальный кабель
  (VB-Audio Virtual Cable) — звуки пойдут «в микрофон».

---

## Структура проекта

```
main.py                     # точка входа
config/
  gamestate_integration_dota_companion.cfg
dota_companion/
  app.py                    # оркестратор: потоки, сигналы, конвейеры
  core/
    async_worker.py         # asyncio-мост (QThread + event loop)
    hotkeys.py              # Win32 RegisterHotKey (Ctrl+Shift+F8 / F9 / F10)
    settings.py             # настройки (JSON, %APPDATA%)
    logger.py
  dota/
    gsi_server.py           # aiohttp: приём GSI JSON от Dota 2
    console_log.py          # tail console.log: чат + Steam ID
    match_state.py          # Player / MatchState, команды
    opendota.py             # ростер и герои из публичного API
  market/
    client.py               # Lolzteam Market API (rate limit, ретраи)
    models.py               # InventoryValue, форматирование валют
  ocr/
    capturer.py             # mss: захват области экрана
    engine.py               # PaddleOCR → Tesseract (поток-очередь)
    translator.py           # Gemini / Google / DeepL / LibreTranslate (async)
  soundpad/
    audio.py                # sounddevice: вывод в выбранное устройство
    triggers.py             # триггеры и сканирование папки звуков
  ui/
    theme.py                # тёмная тема Dota 2 + иконка
    widgets.py              # Card, ToggleSwitch, NeonButton
    main_window.py          # вкладки, трей, статус-бар
    match_tab.py            # панели Radiant/Dire + оценка инвентаря
    chat_tab.py             # история, quick settings, слайдеры
    soundpad_tab.py         # сетка триггеров, громкость, устройство
    soundpad_overlay.py     # плавающий саундпад (Always on Top)
    chat_overlay.py         # прозрачный HUD перевода
    crop_overlay.py         # выбор области чата
```

## Сборка в .exe

```bash
pip install pyinstaller
pyinstaller --noconfirm --windowed --name DotaCompanion ^
  --collect-all paddleocr main.py
```
(без PaddleOCR `--collect-all paddleocr` можно убрать).

## Известные ограничения

- OCR требует **Borderless Windowed**; эксклюзивный fullscreen не
  захватывается (как и в OBS).
- Оценка по Steam-ссылке работает только для профилей с **публичным**
  инвентарём: если инвентарь скрыт, API вернёт ошибку «Инвентарь скрыт».
  Ники из невидимых символов (zero-width) отображаются как «Player …».
- В рейтинговых матчах GSI скрывает Steam ID/ники игроков до фазы
  Strategy Time — до этого момента панели могут быть пустыми, оценки
  появятся, как только матч начнётся.
- OpenDota публикует матч только после его окончания (и с задержкой на
  парсинг) — как запасной источник после конца игры.
