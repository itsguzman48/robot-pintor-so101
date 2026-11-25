# Robot pintor SO-101 — código

Todo se ejecuta desde esta carpeta (en el hackathon era `C:\hackathon`), con el entorno conda `lerobot` (LeRobot 0.7.0, Windows).

```bat
pip install -r requirements.txt
```

## Arrancar (dos rotuladores, configuración final)
```bat
set PORT=COM5
set ROBOT_ID=follower_pintor
python servidor_robot.py --port %PORT% --id %ROBOT_ID% --rotuladores azul=calib_color1.json,modelo_color1.json rojo=calib_color2.json,modelo_color2.json --z-pintar 4 --desplazar rojo=10,-10
```
Después, abrir `../web/robot-dibujante.html` con doble clic. Sin robot: añadir `--simular`.

Probar sin la web (PowerShell):
```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/draw -ContentType "application/json" -InFile pruebas/prueba_colores.json
```

## Archivos
| Archivo | Qué hace |
|---|---|
| `servidor_robot.py` | Servidor HTTP: `POST /draw`, `GET /status`, `POST /stop`, `POST /preview`. Guarda cada dibujo en `trabajos/` |
| `svg_a_trazos.py` | SVG → trazos en mm de la hoja (encajar, limpiar, ordenar, agrupar por color) |
| `dibujar_ik.py` | Mapa calibración→motores, planificación del recorrido y modo de prueba por línea de comandos |
| `dibujar.py` | Interpolación Catmull-Rom de la calibración |
| `geometria.py` | Modelo cinemático del brazo |
| `robot_utils.py` | Conexión con LeRobot y utilidades de motores |
| `calibrar_a_mano.py` | Calibrar un rotulador (45 puntos, a mano) |
| `nivelar.py` | Altura de contacto real e inclinación perpendicular |
| `reajustar_rotulador.py`, `fijar_perpendicular.py` | Ajustar el modelo / la inclinación tras calibrar |
| `medir_retraso.py`, `probar_giro.py`, `mover_muneca.py`, `leer_muneca.py`, `diagnostico_pasitos.py` | Diagnóstico |
| `antiguos/` | Versiones descartadas (solo de consulta) |
| `pruebas/` | Peticiones de ejemplo para `/draw` |
| `calibracion_sin_nivelar/` | Calibraciones de los dos rotuladores **antes** de nivelar. Las buenas (con nivelación) están en el portátil del robot |

Contexto completo del proyecto: [`../docs/CONTEXTO_PROYECTO.md`](../docs/CONTEXTO_PROYECTO.md).
