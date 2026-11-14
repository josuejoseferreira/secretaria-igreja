@echo off
chcp 65001 >nul
net session >nul 2>&1 || (echo Execute como administrador. & pause & exit /b 1)
set "PGHOME=C:\Program Files\PostgreSQL\16"
if not exist "%PGHOME%\data\pg_hba.conf.original" (echo Backup nao encontrado. & pause & exit /b 1)
copy /y "%PGHOME%\data\pg_hba.conf.original" "%PGHOME%\data\pg_hba.conf" >nul
powershell -NoProfile -Command "Get-Service -Name 'postgresql*' | Restart-Service"
echo PostgreSQL voltou a exigir senha.
pause
