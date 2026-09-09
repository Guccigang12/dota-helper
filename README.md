# Dota Companion

Вспомогательный инструмент для Dota 2:

1. **Экранный переводчик чата (OCR)** — захват области чата на экране, OCR (Windows OCR / PaddleOCR / Tesseract), машинный перевод (Google / Gemini / DeepL / LibreTranslate), история сообщений, прозрачный HUD поверх игры, всплывающие уведомления.
2. **Ручной переводчик** — удобный текстовый переводчик с поддержкой горячих клавиш (Ctrl+Enter), авто-перевода при вводе и быстрого копирования.
3. **Саундпад** — плавающий виджет и полноразмерная вкладка с аудио-триггерами, воспроизведение в выбранное устройство (Virtual Audio Cable) с независимой регулировкой громкости.

Стек: **Python 3.10+ · PyQt6 · asyncio (aiohttp) · mss · sounddevice**.

---

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

### OCR (распознавание текста)

**Вариант A — Windows OCR** (рекомендуется, встроен в Windows 10/11, ничего ставить не нужно):
```bash
pip install winocr
```
Языковые пакеты Windows обычно уже установлены. Если нужного языка нет
(например, китайского), установи его в PowerShell от администратора:
```powershell
# Посмотреть установленные языки OCR
Get-WindowsCapability -Online -Name "Language.OCR*"

# Добавить язык (пример: китайский упрощённый)
Add-WindowsCapability -Online -Name "Language.OCR~~~zh-CN~0.0.1.0"
```

**Вариант B — PaddleOCR** (точнее для китайского, ставится отдельно):
```bash
pip install paddlepaddle paddleocr
```

**Вариант C — Tesseract** (fallback): установи
[Tesseract 5](https://github.com/UB-Mannheim/tesseract/wiki) и языки
`chi_sim`, `eng`, `spa`, затем:
```bash
pip install pytesseract
```

---

## Запуск

```bash
python main.py
```

### Глобальные хоткеи

| Клавиша | Действие |
|---|---|
| `Ctrl + Shift + F8` | Выбор области чата для OCR |
| `F9` | Пауза / возобновление OCR |
| `F10` | Выход из приложения |

---

## Модули

### 1. Саундпад
- Воспроизведение звуков (`.mp3`, `.wav`, `.ogg`, `.flac`) в отдельное аудиоустройство (например, Virtual Audio Cable).
- Настройка папки со звуковыми файлами.
- Плавающий оверлей поверх игры для быстрого доступа.

### 2. Чат-переводчик (OCR)
- Интерактивный выбор области чата через `Ctrl + Shift + F8`.
- Распознавание сообщений и моментальный перевод.
- Настройка размера шрифта и времени отображения оверлея поверх экрана.

### 3. Текстовый переводчик
- Ручной ввод и перевод фраз.
- Поддержка провайдеров: Google, Gemini API, DeepL, LibreTranslate.
