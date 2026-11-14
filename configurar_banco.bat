@echo off
chcp 65001 >nul
net session >nul 2>&1 || (echo Clique com o botao direito neste arquivo e escolha Executar como administrador. & pause & exit /b 1)
set "PGHOME=C:\Program Files\PostgreSQL\16"
if not exist "%PGHOME%\data\pg_hba.conf" (echo PostgreSQL 16 nao encontrado em %PGHOME%. Edite a linha PGHOME neste arquivo. & pause & exit /b 1)
if not exist "%PGHOME%\data\pg_hba.conf.original" copy "%PGHOME%\data\pg_hba.conf" "%PGHOME%\data\pg_hba.conf.original" >nul
powershell -NoProfile -Command "(Get-Content -Raw '%PGHOME%\data\pg_hba.conf') -replace 'scram-sha-256','trust' -replace 'md5','trust' | Set-Content -NoNewline -Encoding ASCII '%PGHOME%\data\pg_hba.conf'"
powershell -NoProfile -Command "Get-Service -Name 'postgresql*' | Restart-Service"
timeout /t 6 >nul
"%PGHOME%\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE secretaria"
"%PGHOME%\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE secretaria_test"
echo.
echo Pronto. O PostgreSQL local agora NAO pede senha (so para teste). Se disse que o banco ja existe, tudo bem.
echo Proximo passo: ative o venv e rode  python app.py migrate  e depois iniciar.bat
pause
