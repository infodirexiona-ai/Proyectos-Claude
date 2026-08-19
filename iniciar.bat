@echo off
REM Lanzador rápido: levanta la app y abre el navegador.
REM Doble clic aquí (o desde un acceso directo) en vez de escribir los
REM comandos a mano cada vez.

cd /d "%~dp0"

if not exist ".venv\Scripts\uvicorn.exe" (
    echo No se encontro el entorno virtual ^(.venv^).
    echo Corre primero: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

REM El servidor corre en su propia ventana (identificala por el titulo
REM "EaSII"): para apagarlo, cierra esa ventana o dale Ctrl+C ahi.
start "EaSII" .venv\Scripts\uvicorn.exe app.web.main:app --port 8000

REM Le da un momento al servidor para levantar antes de abrir el navegador.
timeout /t 3 /nobreak >nul
start "" http://localhost:8000
