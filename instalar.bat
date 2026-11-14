@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>&1 || (echo Python nao encontrado. Instale em python.org e marque Add to PATH. & pause & exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || (echo Falha ao instalar as dependencias. & pause & exit /b 1)
if not exist .env (copy .env.example .env >nul & echo Arquivo .env criado. EDITE-o: DATABASE_URL, SECRET_KEY e ADMIN_PASSWORD.) else echo O .env ja existe e foi mantido.
echo.
echo Proximos passos: 1^) crie os bancos secretaria e secretaria_test ^(README^); 2^) edite o .env; 3^) rode: python app.py migrate; 4^) use iniciar.bat
pause
