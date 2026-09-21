@echo off
REM Lo mismo que probar.bat, pero borrando antes lo que hubiera.
REM
REM Sirve para dos cosas: empezar limpio cuando la demo de antes se ha quedado
REM rara, y traerse las novedades. La demo se monta una sola vez y luego se
REM reusa, asi que lo nuevo de esta semana no aparece hasta que se rehace.
REM
REM OJO: se pierde todo lo que hayas metido tu en la demo.
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py -3) || (set PY=python)

echo.
echo   EMPEZAR DE CERO
echo   ---------------
echo   Se borra la demo que hubiera y se monta una nueva con lo ultimo.
echo   Lo que hayas metido tu se pierde.
echo.
choice /c SN /m "Seguimos (S/N)"
if errorlevel 2 goto :fin

if exist ".git\" git pull --ff-only >nul 2>nul
if not exist ".venv\" (
    %PY% -m venv .venv || goto :sinpython
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :error

echo.
echo   Montando la demo desde cero. Un par de minutos.
echo.
python -m thegrill.cli --db sqlite:///demo.db demo --reiniciar
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
