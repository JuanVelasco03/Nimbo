@echo off
rem Nimbo -- doble clic y listo. No abre ninguna consola (pythonw).
rem
rem Primero el Python que trae el instalador en runtime\python\ (ver
rem ajustes.python_embarcado); si no esta, esto es una copia de
rem desarrollo y se usa el Python del sistema. Si ninguno de los dos
rem aparece, cambia la ruta de abajo por la de tu Python con Playwright
rem instalado (el mismo que usa el grabador).
rem
rem Ojo: inicio_automatico.py apunta su shim de la carpeta de Inicio a
rem ESTE archivo, asi que el arranque con Windows depende de que la
rem busqueda de aca abajo sea correcta.

set "NIMBO_PYW=%~dp0runtime\python\pythonw.exe"
if exist "%NIMBO_PYW%" goto arrancar

set "NIMBO_PYW=C:\Python312\pythonw.exe"
if exist "%NIMBO_PYW%" goto arrancar

echo No se encontro ningun Python para arrancar Nimbo.
echo Revisa runtime\python\pythonw.exe o C:\Python312\pythonw.exe
pause
exit /b 1

:arrancar
start "" "%NIMBO_PYW%" "%~dp0nimbo\app.py"
