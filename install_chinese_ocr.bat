@echo off
chcp 65001 >nul
title Установка языкового пакета Windows OCR (Китайский)

echo ======================================================================
echo    Установка языковых пакетов Windows OCR для китайского языка (Dota 2)
echo ======================================================================
echo.

:: Check for Administrator privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [i] Требуются права администратора для установки Windows Capability.
    echo [i] Запрашиваем повышение прав (UAC)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd.exe -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

echo [+] Права администратора получены.
echo.
echo [1/2] Установка упрощенного китайского (Simplified Chinese - zh-CN)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-WindowsCapability -Online -Name 'Language.OCR~~~zh-CN~0.0.1.0'"

echo.
echo [2/2] Установка традиционного китайского (Traditional Chinese - zh-TW)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-WindowsCapability -Online -Name 'Language.OCR~~~zh-TW~0.0.1.0'"

echo.
echo ======================================================================
echo Проверка установленных языков Windows OCR:
echo ======================================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null; [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { Write-Host '  [OK] ' $_.LanguageTag }"

echo.
echo ======================================================================
echo Установка завершена! Теперь перезапустите Dota Companion.
echo ======================================================================
echo.
pause
