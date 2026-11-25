"""Mide cuánto RETRASO tiene cada motor respecto a lo que se le manda (automático, en el aire).

El brazo hace unos círculos pequeños EN EL AIRE (no pinta) y, mientras, lee dónde está cada motor.
Comparando lo pedido con lo real calcula el retraso de cada uno (en segundos) y lo guarda en
retraso.json. Después, dibujar_ik.py y servidor_robot.py mandan cada orden ese tiempo ANTES,
para que los motores lleguen a tiempo y los círculos pequeños salgan redondos.

Uso (con el servidor parado):
  python medir_retraso.py --port %PORT% --id %ROBOT_ID% --calib calib_color1.json --modelo modelo_color1.json
"""
import argparse
import json
import math
import time

import numpy as np

import geometria as G
from dibujar_ik import MapaHibrido, Ritmo, ir_suave, reloj_preciso
from robot_utils import activar_par, conectar, leer_articulaciones, suavizar_motores


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--id", required=True)
    ap.add_argument("--calib", default="calib_color1.json")
    ap.add_argument("--modelo", default="modelo_color1.json")
    ap.add_argument("--altura", type=float, default=25.0, help="mm sobre el papel (en el aire)")
    ap.add_argument("--radio", type=float, default=15.0, help="mm del círculo de prueba")
    ap.add_argument("--vel", type=float, default=30.0, help="mm/s (la misma que al dibujar)")
    ap.add_argument("--vueltas", type=int, default=4)
    ap.add_argument("--rigidez", type=int, default=16)
    ap.add_argument("--suavidad-motor", type=int, default=20)
    ap.add_argument("--hz", type=float, default=50.0)
    ap.add_argument("--salida", default="retraso.json")
    args = ap.parse_args()

    mapa = MapaHibrido(args.calib, args.modelo)
    cx, cy = mapa.ancho / 2, mapa.alto / 2
    periodo = 2 * math.pi * args.radio / args.vel
    n = int(periodo * args.vueltas * args.hz)
    ordenes = []
    for i in range(n):
        a = 2 * math.pi * i / (periodo * args.hz)
        ordenes.append(mapa.articulaciones(cx + args.radio * math.cos(a), cy + args.radio * math.sin(a), args.altura))

    reloj_preciso()
    robot = conectar(args.port, args.id, rigidez=args.rigidez)
    robot.send_action(leer_articulaciones(robot))
    activar_par(robot)
    suavizar_motores(robot, args.suavidad_motor)
    pedido, real, tiempos = [], [], []
    try:
        print(f"Aparta las manos: va a hacer {args.vueltas} círculos de {args.radio:.0f} mm EN EL AIRE.")
        input("Pulsa Enter para empezar...")
        ir_suave(robot, mapa.articulaciones(cx, cy, 45), 3.0, args.hz, vigilar=False)
        ir_suave(robot, ordenes[0], 2.0, args.hz, vigilar=False)
        time.sleep(0.5)
        ritmo = Ritmo(args.hz)
        t0 = time.perf_counter()
        for o in ordenes:
            robot.send_action(o)
            r = leer_articulaciones(robot)
            tiempos.append(time.perf_counter() - t0)
            pedido.append([o[k] for k in G.MOTORES])
            real.append([r[k] for k in G.MOTORES])
            ritmo.esperar()
        ir_suave(robot, mapa.articulaciones(cx, cy, 45), 1.5, args.hz, vigilar=False)
    finally:
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()

    pedido, real, t = np.array(pedido), np.array(real), np.array(tiempos)
    hz_real = len(t) / t[-1]
    salto = int(round(periodo * hz_real))          # descartar la primera vuelta (arranque)
    retrasos = {}
    print(f"\nFrecuencia real: {hz_real:.0f} órdenes/s")
    print(f"{'motor':16s} {'retraso':>8s} {'error sin compensar':>20s} {'error compensado':>17s}")
    for j, k in enumerate(G.MOTORES):
        p, r = pedido[salto:, j], real[salto:, j]
        mejor = (0, np.inf)
        for d in range(0, int(0.6 * hz_real)):
            pp, rr = p[:len(p) - d], r[d:]
            err = rr - pp
            e = float(np.sqrt(np.mean((err - err.mean()) ** 2)))     # quitando lo que cede por el peso
            if e < mejor[1]:
                mejor = (d, e)
        err0 = r - p
        e0 = float(np.sqrt(np.mean((err0 - err0.mean()) ** 2)))
        retrasos[k] = round(mejor[0] / hz_real, 3)
        print(f"{k:16s} {retrasos[k]:7.2f}s {e0:19.2f}° {mejor[1]:16.2f}°")
    with open(args.salida, "w", encoding="utf-8") as f:
        json.dump({"retraso_s": retrasos, "medido_con": {"vel": args.vel, "radio": args.radio,
                                                          "rigidez": args.rigidez,
                                                          "suavidad_motor": args.suavidad_motor}}, f, indent=1)
    print(f"\nGuardado {args.salida}. dibujar_ik.py y servidor_robot.py lo usan solos si está en la carpeta.")


if __name__ == "__main__":
    main()
