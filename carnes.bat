@echo off
REM Arranca el control de carnes desde donde sea. Doble clic y ya.
REM
REM Este fichero no necesita estar dentro del programa: si el programa no esta
REM en el ordenador, se lo baja; si ya esta, se pone al dia. Por eso se puede
REM dejar en el Escritorio y olvidarse de carpetas y de consolas.
setlocal
set "CARPETA=%USERPROFILE%\carnes"
set "RAMA=claude/cool-bohr-jct64l"
set "REPO=https://github.com/Yokekomo/POS-Point-of-sale-system-100-Python.git"

echo.
echo   Control de carnes
echo   -----------------
echo   Carpeta: %CARPETA%
echo.

where git >nul 2>nul || goto :singit

if exist "%CARPETA%\thegrill\" goto :alDia

echo   Bajando el programa. La primera vez tarda un poco...
echo   (no cierres esta ventana: cuando este lista, la demo se abre sola)
git clone --branch "%RAMA%" "%REPO%" "%CARPETA%" || goto :error
goto :arrancar

:alDia
echo   Buscando novedades...
cd /d "%CARPETA%" || goto :error
REM Si esto falla y no se dice, se arranca con lo de la semana pasada y no hay
REM forma de saberlo: un arreglo que no se ha bajado se parece demasiado a un
REM arreglo que no funciona. Asi que el fallo se ensena entero.
git fetch origin "%RAMA%" || goto :sinactualizar
git checkout "%RAMA%" >nul 2>nul
git pull --ff-only origin "%RAMA%" || goto :sinactualizar
echo   Al dia.
goto :arrancar

:sinactualizar
echo.
echo   *** NO SE HA PODIDO ACTUALIZAR ***
echo   Se arranca con lo que ya habia, que puede ser de hace dias. El motivo
echo   esta escrito aqui arriba: copialo y mandamelo.
echo.
pause

:arrancar
cd /d "%CARPETA%" || goto :error
echo.
REM Lo que se va a arrancar, escrito antes de arrancarlo.
for /f "delims=" %%v in ('git log -1 --format^="%%cd  %%h" --date^=format:"%%d/%%m/%%Y %%H:%%M" 2^>nul') do echo   Version: %%v
echo.
call probar.bat
goto :fin

:singit
echo   Te falta git, que es lo que baja el programa. Se instala una vez
echo   desde:
echo.
echo     https://git-scm.com/download/win
echo.
echo   Siguiente, siguiente, siguiente. Cuando acabe, vuelve a hacer doble
echo   clic aqui.
echo.
pause
goto :fin

:error
echo.
echo   Algo ha fallado. Copia lo de arriba y mandamelo.
echo.
pause

:fin
endlocal
