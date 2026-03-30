@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist ".env" (
    echo ========================================
    echo  First launch - setup
    echo ========================================
    echo.
    set /p TG_TOKEN="Telegram bot token (from @BotFather): "
    echo TG_BOT_TOKEN=!TG_TOKEN!> .env
    echo.
    echo .env created!
    echo.
)

echo Installing dependencies...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo ERROR: failed to install dependencies.
    pause
    exit /b 1
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    if not exist "ffmpeg\ffmpeg.exe" (
        echo Downloading FFmpeg...
        powershell -Command ^
            "Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile 'ffmpeg.zip'; " ^
            "Expand-Archive 'ffmpeg.zip' 'ffmpeg_temp' -Force; " ^
            "$bin = (Get-ChildItem 'ffmpeg_temp' -Recurse -Filter 'ffmpeg.exe')[0].DirectoryName; " ^
            "New-Item 'ffmpeg' -ItemType Directory -Force | Out-Null; " ^
            "Copy-Item (Join-Path $bin '*') 'ffmpeg' -Force; " ^
            "Remove-Item 'ffmpeg_temp','ffmpeg.zip' -Recurse -Force"
        if not exist "ffmpeg\ffmpeg.exe" (
            echo ERROR: failed to download FFmpeg.
            pause
            exit /b 1
        )
        echo FFmpeg installed!
    )
)
set PATH=%~dp0ffmpeg;%PATH%

if not exist ".chrome_profile" (
    echo.
    echo ========================================
    echo  YouTube login (one-time)
    echo ========================================
    echo Chrome will open.
    echo Log into your Google account on YouTube,
    echo then CLOSE Chrome.
    echo.
    python setup_cookies.py
    if not exist ".chrome_profile" (
        echo ERROR: cookie setup failed.
        pause
        exit /b 1
    )
)

echo.
echo Starting bot...
echo.
python bot.py
pause
