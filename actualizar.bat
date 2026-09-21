@echo off
REM Traer la última versión. Doble clic aquí, sin escribir nada.
setlocal
cd /d "%~dp0"

if not exist ".git\" goto :sinrepo

echo.
echo   Buscando novedades...
git pull --ff-only || goto :conflicto
echo.
echo   Al dia. Si ha cambiado algo de las librerias, se instala ahora:
if exist ".venv\" call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt
echo.
echo   Listo. Arranca con probar.bat
echo.
pause
goto :fin

:sinrepo
echo.
echo   Esta carpeta se bajo como ZIP, asi que no sabe actualizarse sola.
echo   Baja el ZIP otra vez desde:
echo.
echo     https://github.com/Yokekomo/POS-Point-of-sale-system-100-Python/tree/claude/cool-bohr-jct64l
echo.
echo   (boton verde Code - Download ZIP). Tus datos de la demo estan en
echo   demo.db: copia ese fichero a la carpeta nueva y no pierdes nada.
echo.
pause
goto :fin

:conflicto
echo.
echo   No he podido actualizar sin pisar cambios tuyos. Copia lo de arriba
echo   y mandamelo.
echo.
pause

:fin
endlocal
