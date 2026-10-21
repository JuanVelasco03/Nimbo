@echo off
rem Nimbo -- doble clic y listo. No abre ninguna consola (pythonw).
rem Si esto no hace nada, es que C:\Python312\pythonw.exe no esta: cambia
rem la ruta de abajo por la de tu Python con Playwright instalado
rem (el mismo que usa el grabador).
start "" "C:\Python312\pythonw.exe" "%~dp0nimbo\app.py"
