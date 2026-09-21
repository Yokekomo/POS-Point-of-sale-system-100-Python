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
git fetch origin "%RAMA%" >nul 2>nul
git checkout "%RAMA%" >nul 2>nul
git pull --ff-only origin "%RAMA%" >nul 2>nul
if errorlevel 1 echo   (no se ha podido actualizar; se arranca con lo que hay)

:arrancar
cd /d "%CARPETA%" || goto :error
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
