"""Prueba del motor de giro de muñeca (wrist_roll) para el cambio de color.

1. Sube el brazo a una postura segura (centro de la hoja, 60 mm sobre el papel).
2. Gira la muñeca entre las posiciones de color (cada 90°) varias veces,
   llegando a cada una desde los dos sentidos.
3. Mide con el encoder lo bien que vuelve a cada posición (repetibilidad y holgura).

Uso:
  python probar_giro.py --port %PORT% --id %ROBOT_ID%
Opciones:
  --colores 4      cuántas posiciones (cada 90°)
  --ciclos 5       cuántas vueltas completas
"""
import argparse
import time

import numpy as np

from dibujar_ik import MapaHibrido, Ritmo, reloj_preciso, ir_suave
from robot_utils import activar_par, conectar, leer_articulaciones, suavizar_motores

ROLL = "wrist_roll.pos"


def rango_roll(robot):
    """Grados alcanzables del wrist_roll según la calibración del follower."""
    try:
        cal = robot.calibration["wrist_roll"]
        medio = (cal.range_max - cal.range_min) / 2 * 360 / 4095
        return -medio, medio
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--id", required=True)
    ap.add_argument("--calib", default="calib_activa_completa.json")
    ap.add_argument("--modelo", default="modelo_brazo.json")
    ap.add_argument("--colores", type=int, default=4)
    ap.add_argument("--ciclos", type=int, default=5)
    ap.add_argument("--altura", type=float, default=60.0, help="mm sobre el papel durante la prueba")
    ap.add_argument("--duracion-giro", type=float, default=1.5, help="segundos por cada 90°")
    ap.add_argument("--hz", type=float, default=100.0)
    ap.add_argument("--inicio", type=float, default=None,
                    help="ángulo del color 1 en grados (por defecto: el extremo bajo del rango + margen)")
    ap.add_argument("--rigidez", type=int, default=16)
    ap.add_argument("--paso", type=float, default=90.0, help="grados entre colores")
    args = ap.parse_args()

    mapa = MapaHibrido(args.calib, args.modelo)
    segura = mapa.articulaciones(mapa.ancho / 2, mapa.alto / 2, args.altura)
    r0 = segura[ROLL]

    reloj_preciso()
    robot = conectar(args.port, args.id, rigidez=args.rigidez)
    activar_par(robot)
    suavizar_motores(robot, 20)

    lim = rango_roll(robot)
    if lim:
        print(f"wrist_roll puede ir de {lim[0]:.0f}° a {lim[1]:.0f}°. Color 1 está en {r0:.1f}°.")
    # Color 1 en el primer extremo del giro (o donde digas con --inicio) y el resto cada 90°
    margen = 5.0
    if args.inicio is not None:
        inicio = args.inicio
    elif lim:
        inicio = lim[0] + margen
    else:
        inicio = r0
    objetivos = [inicio + args.paso * i for i in range(args.colores)]
    if lim and max(objetivos) > lim[1] - 1:
        fuera = [round(o, 1) for o in objetivos if o > lim[1] - 1]
        raise SystemExit(f"No caben: {fuera} se sale del rango (máx {lim[1]:.0f}°). Usa --inicio más bajo.")
    print(f"Posiciones de color: {[round(o, 1) for o in objetivos]}")

    medidas = {i: [] for i in range(len(objetivos))}
    try:
        segura = dict(segura)
        segura[ROLL] = objetivos[0]
        print("\nSubiendo a la postura segura (el rotulador va a girar, que no haya nada cerca)...")
        ir_suave(robot, segura, 4.0, args.hz, vigilar=False)
        time.sleep(1.0)
        actual = dict(segura)
        r_ini = leer_articulaciones(robot)[ROLL]
        print(f"  color 1 (inicio) pedido {objetivos[0]:7.2f}°  real {r_ini:7.2f}°  error {r_ini - objetivos[0]:+.2f}°")
        # recorrido: ida 0,1,2,3 y vuelta 3,2,1,0 -> cada posición se alcanza desde los dos sentidos
        idx = list(range(len(objetivos)))
        vuelta = idx + idx[-2::-1]           # 0,1,2,3,2,1,0
        secuencia = vuelta + vuelta[1:] * (args.ciclos - 1)
        ultimo = 0
        for n, i in enumerate(secuencia[1:], 1):
            destino = dict(actual)
            destino[ROLL] = objetivos[i]
            pasos = abs(i - ultimo)
            ir_suave(robot, destino, args.duracion_giro * max(1, pasos) * args.paso / 90, args.hz, vigilar=False)
            time.sleep(0.6)
            real = leer_articulaciones(robot)[ROLL]
            sentido = "+" if i > ultimo else "-"
            medidas[i].append((real - objetivos[i], sentido))
            print(f"  color {i + 1} ({sentido}) pedido {objetivos[i]:7.2f}°  real {real:7.2f}°  "
                  f"error {real - objetivos[i]:+.2f}°")
            if abs(real - objetivos[i]) > 3:
                print("      ^ no llega: o tropieza con algo (cable, pieza) o el peso del rotulador le puede")
            actual = destino
            ultimo = i
        print("\nVolviendo al color 1...")
        destino = dict(actual)
        destino[ROLL] = objetivos[0]
        ir_suave(robot, destino, args.duracion_giro * max(1, ultimo) * args.paso / 90, args.hz, vigilar=False)
    finally:
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()

    print("\nResumen (repetibilidad = cuánto varía al volver a la misma posición):")
    for i, ms in medidas.items():
        if not ms:
            continue
        e = np.array([m[0] for m in ms])
        mas = [m[0] for m in ms if m[1] == "+"]
        menos = [m[0] for m in ms if m[1] == "-"]
        holgura = abs(np.mean(mas) - np.mean(menos)) if mas and menos else float("nan")
        print(f"  color {i + 1}: error medio {e.mean():+.2f}°  dispersión {e.max() - e.min():.2f}°  "
              f"holgura entre sentidos {holgura:.2f}°")
    print("Con el rotulador a ~R mm del eje, 1° de error ≈ R/57 mm en la punta (R=60 mm -> ~1 mm).")


if __name__ == "__main__":
    main()
