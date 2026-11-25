@echo off
REM Prueba del círculo con el rotulador sujeto con cinta.
REM Uso (desde C:\hackathon, con el entorno lerobot activado):
REM   probar_circulo.bat            -> primero en el aire y luego pintando
REM   probar_circulo.bat -4         -> igual, pero apretando 4 mm (por defecto 3)
REM Parada de emergencia: cualquier tecla.

set PRESION=%1
if "%PRESION%"=="" set PRESION=-3

if "%PORT%"=="" set PORT=COM5
if "%ROBOT_ID%"=="" set ROBOT_ID=follower_pintor

set OPCIONES=--port %PORT% --id %ROBOT_ID% --calib calib_activa_completa.json --figura circulo --hz 100 --vel 30 --acel 200

echo.
echo === 1/2 Circulo EN EL AIRE (mira que vaya suave y redondo) ===
python dibujar_ik.py %OPCIONES% --en-el-aire
if errorlevel 1 goto fin

echo.
set /p SEGUIR=Pinta de verdad apretando %PRESION% mm? (s/n):
if /i not "%SEGUIR%"=="s" goto fin

echo.
echo === 2/2 Circulo PINTANDO a z=%PRESION% mm ===
python dibujar_ik.py %OPCIONES% --z-pintar %PRESION%

:fin
echo.
echo Fin de la prueba.
