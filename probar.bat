@echo off
REM Probar el control de carnes en Windows, sin saber de Python.
REM Se hace doble clic en este fichero, o se escribe  probar.bat  en la consola.
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py -3) || (set PY=python)

echo.
echo   Preparando la demo.
echo.
echo   LA PRIMERA VEZ TARDA UN PAR DE MINUTOS. No cierres esta ventana
echo   y no abras todavia el navegador: cuando este lista se abre sola.
echo.

REM Si se bajo con git, se trae lo ultimo antes de arrancar. Si no se puede,
REM se dice: arrancar con lo de hace dias sin avisar confunde mas que ayuda.
if exist ".git\" (
    git pull --ff-only || echo   (no se ha podido actualizar; se arranca con lo que hay^)
    for /f "delims=" %%v in ('git log -1 --format^="%%cd  %%h" --date^=format:"%%d/%%m/%%Y %%H:%%M" 2^>nul') do echo   Version: %%v
)
if not exist ".venv\" (
    %PY% -m venv .venv || goto :sinpython
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :error

echo.
echo   Montando la demo y arrancando. Ahora si: un par de minutos.
echo.
python -m thegrill.cli --db sqlite:///demo.db demo
echo.
echo   La demo se ha parado. Si ha sido un fallo, esta escrito aqui arriba:
echo   copialo y mandamelo.
echo.
pause
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
