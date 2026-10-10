#Requires -RunAsAdministrator
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "   Установка языковых пакетов Windows OCR для китайского языка" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/2] Установка упрощенного китайского (Simplified Chinese - zh-CN)..." -ForegroundColor Yellow
Add-WindowsCapability -Online -Name "Language.OCR~~~zh-CN~0.0.1.0"

Write-Host "`n[2/2] Установка традиционного китайского (Traditional Chinese - zh-TW)..." -ForegroundColor Yellow
Add-WindowsCapability -Online -Name "Language.OCR~~~zh-TW~0.0.1.0"

Write-Host "`n======================================================================" -ForegroundColor Cyan
Write-Host "Установленные языки Windows OCR:" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Cyan

[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object {
    Write-Host "  [OK] $($_.LanguageTag)" -ForegroundColor Green
}

Write-Host "`nУстановка завершена успешно!" -ForegroundColor Green
