# Robot pintor SO-101 — contexto completo del proyecto

Equipo **CapillaSixt.IA** · Hackathon LeRobot Bilbao II (Physical AI Hackathon), La Perrera, 20–22 nov 2026.

> Documentación técnica del proyecto: cómo funciona, qué hace cada archivo, cómo se usa y qué
> aprendimos. Refleja el estado del proyecto al terminar el hackathon.

---

## 1. Qué hace

Una persona escribe en una web lo que quiere ("un gato", "una flor en una maceta"), un modelo de
lenguaje genera un **SVG de líneas sencillas**, la persona elige un boceto y pulsa **Dibujar**: el brazo
**SO-101** lo dibuja en un folio con rotulador. Hay **dos rotuladores** (azul y rojo) montados en la
pinza a 90° uno del otro; para cambiar de color el brazo gira la muñeca (`wrist_roll`).

No se usa aprendizaje por imitación: se **calibra** la hoja (posturas de motor en una rejilla de
puntos) y se **interpola** entre ellos.

---

## 2. Hardware y entorno

| Cosa | Detalle |
|---|---|
| Brazo | SO-101 follower, servos Feetech STS3215 (encoder 4096 pasos/vuelta = 0,088° por paso) |
| Leader | SO-101 leader en COM6 (solo se usó para una calibración; ya no hace falta) |
| Puerto follower | `COM5` · id `follower_pintor` (variables `%PORT%` y `%ROBOT_ID%` en la terminal) |
| PC | Windows 11, Miniforge, entorno conda `lerobot`, Python 3.12 |
| LeRobot | **0.7.0** con `lerobot[feetech]`. Clase `lerobot.robots.so_follower.SOFollower` (el módulo antiguo `so101_follower` no existe en 0.7). `bus.enable_torque(motores)` / `bus.disable_torque(motores)` aceptan lista de motores. |
| Calibración de motores LeRobot | `%USERPROFILE%\.cache\huggingface\lerobot\calibration\robots\so_follower\follower_pintor.json`, `use_degrees=True` |
| Carpeta de trabajo | `C:\hackathon` (todos los .py y .json van aquí) |
| Dependencias extra | `pip install svgelements scipy matplotlib` |
| Hoja | Plantilla impresa A4 horizontal con cruces cada 17 mm (columnas c0–c16, filas f0–f10). El robot está del lado de f10. |
| Rotuladores | Azul apuntando abajo con la muñeca a −5,7°; rojo apuntando abajo con la muñeca a 90°. Sujetos con cinta americana. **Si se mueven en la cinta, hay que recalibrar ese color.** |

Límites medidos del `wrist_roll`: no pasa de **−84°** (tope); rango útil **−80° a 170°**, repetibilidad excelente, ~1° de holgura entre sentidos.

---

## 3. Coordenadas

- **Hoja (mm):** `x` de 0 a 272 (c0 → c16), `y` de 0 a 136 (f0 → f8). `y` crece hacia el robot. La fila f10 no se calibró (el brazo llega mal).
- **Altura `z` (mm):** 0 = tocando el papel (tras nivelar), positivo = arriba.
- **Motores:** `shoulder_pan.pos`, `shoulder_lift.pos`, `elbow_flex.pos`, `wrist_flex.pos`, `wrist_roll.pos`, `gripper.pos` (grados).

---

## 4. Cómo funciona el dibujo (pipeline)

```
web (robot-dibujante.html)
  │ genera SVG con un LLM (Groq / Gemini / OpenRouter / Claude), viewBox 0 0 100 100
  │ POST /draw {svg, titulo, opciones}
  ▼
servidor_robot.py  (HTTP en localhost:8000, CORS abierto)
  1. svg_a_trazos.py   SVG → polilíneas en mm: muestrea curvas, encaja y centra en la hoja (máx 130 mm),
                       agranda detalles cerrados < 8 mm, convierte en PUNTO los < 6,5 mm,
                       simplifica (Douglas-Peucker), ordena trazos (vecino más cercano) y agrupa por color
  2. Planificador      por cada color: recorrido 3D (bajar despacio, pintar, subir, viajar),
                       perfil de velocidad (frena en curvas cerradas), muestreo a 100 Hz,
                       ángulos de motor con el mapa de ese rotulador. Entre colores: gira la muñeca
                       arriba y luego se coloca encima del primer trazo del otro color.
                       Si algo no se alcanza o hay un salto > 2° → 422 y NO se mueve
  3. Brazo             manda las órdenes a ritmo fijo; parada con /stop o cualquier tecla
```

**Mapa de un rotulador (`MapaHibrido` en `dibujar_ik.py`):**
- **Sobre el papel:** interpolación Catmull-Rom de las posturas calibradas (capa `abajo` de la calibración). Es la "verdad".
- **Nivelación:** la calibración se hizo apretando; `nivelar.py` mide la altura de contacto real (plano por la hoja) y se aplica como subida vertical con el modelo.
- **Subir/bajar (`--subida hombro`, por defecto):** gira solo el hombro y la muñeca gira lo contrario para que el rotulador no cambie de inclinación. Cerca del papel es casi vertical (1 mm arriba ≈ 0,3 mm de lado).
- **Modelo del brazo (`geometria.py`):** cinemática con parámetros ajustados por mínimos cuadrados (L1≈102 mm, L2≈134 mm, base en x≈123, y≈386). Solo se usa para la nivelación y algunas utilidades; tiene ~4–6 mm de error, por eso NO se usa para posicionar sobre el papel.

---

## 5. Archivos

### En uso (los importantes)

| Archivo | Qué es |
|---|---|
| `robot-dibujante.html` | La web. Genera SVG con un LLM, vista previa con colores reales, selector **1 color / 2 colores**, botón Dibujar / Parar, progreso "Trazo X de N". Ajustes: proveedor, modelo, API key, **URL base del robot** (`http://localhost:8000`) y system prompt (guardado en localStorage `robot_sistema_v4`). |
| `servidor_robot.py` | Servidor HTTP: `POST /draw`, `GET /status`, `POST /stop`, `POST /preview`. Un dibujo a la vez. Guarda cada dibujo en `trabajos/` (dataset). |
| `svg_a_trazos.py` | SVG → trazos en mm (ver pipeline). También se puede usar suelto con `--png` para ver la vista previa. |
| `dibujar_ik.py` | Núcleo: `MapaHibrido`, planificación (`construir_recorrido`, `redondear_y_muestrear`, `perfil_velocidad`, `muestrear_en_tiempo`, `insertar_pausas`) y un modo de línea de comandos para dibujar figuras de prueba (`--figura cuadrado|circulo|circulos|estrella|rejilla`) o un JSON de trazos (`--trazos`). |
| `dibujar.py` | `MapaHoja`: lee la calibración y hace la interpolación Catmull-Rom. |
| `geometria.py` | Modelo cinemático del brazo (`fk`, `Modelo._ik`, `angulo_pinza`, ajuste). |
| `robot_utils.py` | `conectar`, `leer_articulaciones`, `activar_par`, `desactivar_par`, `suavizar_motores` (registro Acceleration). |
| `calibrar_a_mano.py` | Calibración de un rotulador moviendo el brazo con la mano: ENTER suelta, colocas la punta en la cruz, ESPACIO se sujeta solo, ENTER guarda. `--cada 1 --filas 5` = 45 puntos (9×5, cada 34 mm). `--roll 90` para el rojo. |
| `reajustar_rotulador.py` | Ajusta en el modelo solo dónde está la punta (e, pa, pb) a partir de una calibración. |
| `fijar_perpendicular.py` | Iguala la inclinación del rotulador en todos los puntos a la de un punto de referencia. |
| `nivelar.py` | En 5 puntos colocas a mano la punta rozando y perpendicular; guarda el plano de contacto (`nivel`) e iguala la inclinación. |
| `medir_retraso.py` | Mide el retraso de cada motor (círculos en el aire) → `retraso.json`. **Desactivado por defecto** (ver §8). |
| `probar_giro.py`, `mover_muneca.py`, `leer_muneca.py` | Pruebas del giro de muñeca (rango, repetibilidad, registros). |
| `diagnostico_pasitos.py` | Mide si los motores van a saltos (resolución, error de seguimiento). |

### Datos actuales

| Archivo | Qué es |
|---|---|
| `calib_color1.json` | Calibración del **azul** (45 puntos, muñeca −5,7°), ya con inclinación fijada y nivelación (`nivel`). |
| `calib_color2.json` | Calibración del **rojo** (45 puntos, muñeca 90°), igual. |
| `modelo_color1.json`, `modelo_color2.json` | Modelo del brazo con la punta de cada rotulador. |
| `*_antes_nivel.json`, `*_antes_perpendicular.json`, `*_sin_nivel.json` | Copias de seguridad automáticas. |
| `retraso.json` | Retrasos medidos: pan 0,16 s, hombro 0,32 s, codo 0,16 s, muñeca 0,14 s (no se usa por defecto). |
| `prueba_colores.json`, `prueba_registro.json` | Peticiones de prueba para `/draw` (cara con dos colores; cruces azul+rojo para medir el desfase). |
| `trabajos/` | Un `.svg` + `.json` por dibujo (trazos en mm, parámetros, resultado). Es el dataset para la entrega. |

### Antiguos (no usar salvo para consultar)

`calibrar_hoja.py`, `limpiar_calib.py`, `revisar_calib.py` (primera calibración con el par quitado, cedía por el peso) ·
`grabar_barrido.py`, `ajustar_modelo.py` (ajuste del modelo completo) · `medir_caida.py`, `calibrar_activo.py`,
`calibrar_con_leader.py`, `completar_calib.py` (calibraciones intermedias) · `ensenar_levantar*.py` (subida enseñada, sustituida por `--subida hombro`) ·
`probar_circulo.bat` · `calib_activa*.json`, `modelo_brazo*.json` (rotulador antiguo).

---

## 6. Cómo se usa

**Calibrar un rotulador desde cero** (con el servidor parado):
```bat
python calibrar_a_mano.py --port %PORT% --id %ROBOT_ID% --cada 1 --filas 5 --salida calib_color1.json
python calibrar_a_mano.py --port %PORT% --id %ROBOT_ID% --cada 1 --filas 5 --roll 90 --salida calib_color2.json
```
Luego, por cada color: copiar un modelo base a `modelo_colorN.json`, `reajustar_rotulador.py --calib … --modelo …`, y `nivelar.py --calib … --modelo …`.

**Arrancar el servidor con los dos colores (configuración actual):**
```bat
python servidor_robot.py --port %PORT% --id %ROBOT_ID% --rotuladores azul=calib_color1.json,modelo_color1.json rojo=calib_color2.json,modelo_color2.json --z-pintar 4 --desplazar rojo=10,-10
```
Sin robot, para probar la web: añadir `--simular`.

**Enviar un dibujo de prueba (PowerShell):**
```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/draw -ContentType "application/json" -InFile prueba_registro.json
```

**Probar figuras sin la web:**
```bat
python dibujar_ik.py --port %PORT% --id %ROBOT_ID% --calib calib_color1.json --modelo modelo_color1.json --figura circulos --z-pintar 4
```
Siempre se puede probar primero con `--en-el-aire` (no toca el papel) o `--simular` (no mueve nada).

---

## 7. Parámetros que importan (servidor)

| Opción | Valor | Qué hace |
|---|---|---|
| `--z-pintar` | 4 (en pruebas; con buena nivelación debería bastar 0–2) | mm sobre el papel al pintar. Más alto = aprieta menos |
| `--z-viaje` / `--z-segura` | 45 / 70 | altura al viajar entre trazos / al empezar, terminar y cambiar de color |
| `--vel` / `--vel-viaje` | 30 / 60 mm/s | pintando / en el aire |
| `--acel-curva` | 15 | frena en curvas cerradas: círculo de 12 mm ≈ 9 mm/s, de 120 mm ≈ 30 mm/s |
| `--z-aprox` / `--vel-aprox` | 4 mm / 10 mm/s | los últimos mm de bajada y primeros de subida, despacio |
| `--pausa-bajar` / `--pausa-subir` | 0,08 / 0,03 s | quieto al tocar / antes de levantar (más largo = charcos de tinta) |
| `--desplazar rojo=dx,dy` | 10,-10 | corrige dónde cae el rojo (mm; dx + hacia c16, dy + hacia f10) |
| `--min-detalle` / `--punto-max` | 8 / 6,5 mm | agrandar detalles pequeños / convertir en punto los diminutos |
| `--tamano` | 130 mm | lado máximo del dibujo (la zona útil es 272×136) |
| `--girar` | 0 | girar el dibujo (0/90/180/270) si sale al revés para el público |
| `--rigidez` / `--suavidad-motor` | 16 / 20 | coeficiente P de los servos (32 vibraba) / rampa interna de aceleración |
| `--hz` | 100 | órdenes por segundo |

---

## 8. Lo que hemos aprendido (no repetir errores)

- **Calibrar con el par quitado no sirve:** al sujetarse solo, el brazo cede por el peso y la altura no cuadra. Calibrar siempre con el robot sujetándose solo (`calibrar_a_mano.py` hace ESPACIO = se sujeta).
- **Interpolar ángulos entre puntos separados 68 mm deforma las curvas** (hasta 9 mm). Con 34 mm (45 puntos) los círculos grandes ya salen redondos.
- **El modelo cinemático tiene 4–6 mm de error**; no usarlo para posicionar en el papel. El modo "modelo + corrección" (`--mapa residuo`) salió peor en la realidad.
- **Al calibrar a mano se aprieta sin querer** → el robot aprieta de más, roza, se dobla y pinta al subir. Solución: `nivelar.py`.
- **Bajar a la velocidad de viaje hacía rebotar la punta** → ahora los últimos 4 mm van a 10 mm/s.
- **Holgura de engranajes (~1°, 3–4 mm en la punta):** se nota en esquinas y círculos pequeños, y cambia de lado si se dibuja al revés. Hay compensación (`--holgura`) pero no se llegó a ajustar.
- **Retraso de los motores (0,15–0,32 s):** deforma los círculos pequeños. La anticipación (`--retraso retraso.json`) mejoró el seguimiento en el aire, pero al empezar los trazos hacía rayas → **desactivada**; ahora se va más despacio en curvas (`--acel-curva`).
- **Capas que hemos quitado por defecto** (siguen en el código): pausa en esquinas, pausa arriba, compensar subida, holgura, anticipación. Antes de añadir más lógica, comprobar que no se pisa con lo que hay.
- **El cambio de color funciona:** cada color con su calibración; el desfase que queda se corrige con `--desplazar`.

---

## 9. Pendiente

1. Ajustar `--z-pintar` para que no apriete (y si cambia mucho por zonas, repetir `nivelar.py` con los dos colores).
2. Comprobar que el dibujo sale derecho visto por el público (`--girar 180` si no).
3. Ensayar la demo completa desde la web con 2–3 dibujos que salgan bien (prompt con formas grandes y pocos detalles).
4. Entregables del domingo: vídeo de la demo, código (estos archivos), dataset (`trabajos/`) y presentación.

---

## 10. Normas para quien toque el código

- Responder y comentar en **español**; comandos para **Windows** (cmd o PowerShell; en PowerShell `curl` no es curl → usar `Invoke-RestMethod`).
- Seguridad: velocidades bajas por defecto, probar `--en-el-aire` o `--simular` antes de pintar, parada de emergencia con cualquier tecla.
- No mover el robot sin la comprobación previa de alcance y saltos.
- La API de LeRobot cambia entre versiones: comprobar nombres de clases y registros con la 0.7 instalada.
- Preferir **menos lógica y bien entendida** a más capas de compensación.
