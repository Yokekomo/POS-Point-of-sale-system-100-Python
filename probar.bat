@echo off
REM Probar el control de carnes en Windows, sin saber de Python.
REM Se hace doble clic en este fichero, o se escribe  probar.bat  en la consola.
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py -3) || (set PY=python)

echo.
echo   Preparando la demo. La primera vez tarda un par de minutos.
echo.

REM Si se bajo con git, se trae lo ultimo antes de arrancar.
if exist ".git\" git pull --ff-only >nul 2>nul
if not exist ".venv\" (
    %PY% -m venv .venv || goto :sinpython
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :error

echo.
echo   Listo. Arrancando...
echo.
python -m thegrill.cli --db sqlite:///demo.db demo
goto :fin

:sinpython
echo.
echo   No encuentro Python. Se instala desde https://www.python.org/downloads/
echo   marcando la casilla "Add python.exe to PATH" al instalarlo.
echo.
pause
goto :fin

:error
echo.
echo   Algo ha fallado al instalar. Copia lo de arriba y mandamelo.
echo.
pause

:fin
endlocal
