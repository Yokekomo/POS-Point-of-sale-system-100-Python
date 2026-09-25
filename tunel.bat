@echo off
REM Arranca el control de carnes y lo saca a internet por un tunel de
REM Cloudflare. Doble clic y ya: sale una direccion https que se abre desde
REM cualquier movil, este donde este, sin tocar el router ni abrir puertos.
REM
REM Como funciona: el programa de Cloudflare llama hacia fuera desde este
REM ordenador, y las visitas vuelven por esa misma llamada. Por eso no hay
REM nada que configurar en el router ni que registrarse en ningun sitio.
REM
REM Este fichero no necesita estar dentro del programa: si no esta en el
REM ordenador se lo baja, y si ya esta lo pone al dia. Puede vivir en el
REM Escritorio.
setlocal
set "CARPETA=%USERPROFILE%\carnes"
set "RAMA=claude/cool-bohr-jct64l"
set "REPO=https://github.com/Yokekomo/POS-Point-of-sale-system-100-Python.git"
set "PUERTO=8001"
set "CF=%CARPETA%\cloudflared.exe"

echo.
echo   Control de carnes, por un tunel
echo   -------------------------------
echo   Carpeta: %CARPETA%
echo.

where git >nul 2>nul || goto :singit

if exist "%CARPETA%\thegrill\" goto :alDia

echo   Bajando el programa. La primera vez tarda un poco...
git clone --branch "%RAMA%" "%REPO%" "%CARPETA%" || goto :error
goto :cloudflared

:alDia
echo   Buscando novedades...
cd /d "%CARPETA%" || goto :error
REM Si esto falla y no se dice, se arranca con lo de la semana pasada y no hay
REM forma de saberlo: un arreglo que no se ha bajado se parece demasiado a un
REM arreglo que no funciona.
git fetch origin "%RAMA%" || goto :sinactualizar
git checkout "%RAMA%" >nul 2>nul
git pull --ff-only origin "%RAMA%" || goto :sinactualizar
echo   Al dia.
goto :cloudflared

:sinactualizar
echo.
echo   *** NO SE HA PODIDO ACTUALIZAR ***
echo   Se arranca con lo que ya habia, que puede ser de hace dias. El motivo
echo   esta escrito aqui arriba: copialo y mandamelo.
echo.
pause

:cloudflared
where cloudflared >nul 2>nul && set "CF=cloudflared"
if /i not "%CF%"=="cloudflared" if not exist "%CF%" (
    echo   Bajando cloudflared. Son unos veinte megas y es una sola vez.
    powershell -NoProfile -c "iwr 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile '%CARPETA%\cloudflared.exe'" || goto :sintunel
)

echo.
echo   Arrancando el programa en otra ventana. LA PRIMERA VEZ TARDA UN PAR
echo   DE MINUTOS montando el mes de trabajo. No cierres ninguna de las dos
echo   ventanas.
echo.
start "Control de carnes" /d "%CARPETA%" cmd /k probar.bat

echo   Esperando a que conteste en el puerto %PUERTO%...
REM Se espera a que el programa conteste de verdad antes de abrir el tunel.
REM Abriendolo antes, Cloudflare da la direccion y quien la abre se encuentra
REM un error: parece que el tunel esta roto cuando lo que pasa es que el
REM programa todavia se esta montando.
powershell -NoProfile -c "$fin=(Get-Date).AddMinutes(8); while((Get-Date) -lt $fin){ try{ $c=New-Object Net.Sockets.TcpClient('127.0.0.1',%PUERTO%); $c.Close(); exit 0 } catch { Start-Sleep -Seconds 2 } }; exit 1"
if errorlevel 1 goto :notarranco

echo.
echo   ============================================================
echo    El programa ya contesta. Abriendo el tunel.
echo.
echo    La direccion sale aqui abajo, dentro de un recuadro y
echo    acabada en  .trycloudflare.com  . Esa es la que se manda
echo    al movil.
echo.
echo    Se cierra con Ctrl+C en esta ventana.
echo   ============================================================
echo.
"%CF%" tunnel --url http://127.0.0.1:%PUERTO%

echo.
echo   El tunel se ha cerrado. El programa sigue en la otra ventana: para
echo   pararlo del todo, Ctrl+C alli tambien.
echo.
pause
goto :fin

:notarranco
echo.
echo   El programa no ha llegado a contestar en ocho minutos. Mira la otra
echo   ventana: el motivo esta escrito alli. No se abre el tunel, porque
echo   daria una direccion que no lleva a ninguna parte.
echo.
pause
goto :fin

:sintunel
echo.
echo   No se ha podido bajar cloudflared. Sin el no hay tunel, pero el
echo   programa se puede usar igual en este ordenador y en la wifi de casa:
echo   doble clic en carnes.bat.
echo.
pause
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
