@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo Execute instalar.bat primeiro. & pause & exit /b 1)
if not exist .env (echo Arquivo .env nao encontrado. Execute instalar.bat. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python app.py check || (pause & exit /b 1)
echo.
echo Sistema em http://localhost:5000  ^(Ctrl+C para parar^)
start "" http://localhost:5000
python app.py
pause
