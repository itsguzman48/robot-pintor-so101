"""Diagnóstico de los "pasitos": ¿es el motor o es la mecánica?

Hace UNA línea recta lenta de izquierda a derecha EN EL AIRE (15 mm sobre el papel)
y en cada instante guarda lo que se le pide a cada motor y lo que el motor dice que tiene.

- Si el ENCODER también va a saltos -> es el control del motor (rigidez, zona muerta...).
- Si el encoder va suave pero tú ves pasitos -> es mecánico (holgura de engranajes,
  piezas que flexan, rotulador mal sujeto). Eso no se arregla con software.

Uso:
  python diagnostico_pasitos.py --port %PORT% --id %ROBOT_ID%
Guarda diagnostico.csv y saca un resumen por pantalla.
"""
import argparse
import csv
import time

import numpy as np

import geometria as G
from dibujar_ik import MapaHibrido, Ritmo, reloj_preciso, ir_suave
from robot_utils import activar_par, conectar, leer_articulaciones, suavizar_motores

REGISTROS = ["P_Coefficient", "I_Coefficient", "D_Coefficient",
             "CW_Dead_Zone", "CCW_Dead_Zone", "Acceleration"]


def leer_registros(robot):
    for m in robot.bus.motors:
        if m == "gripper":
            continue
        vals = []
        for r in REGISTROS:
            try:
                try:
                    v = robot.bus.read(r, m, normalize=False)
                except TypeError:
                    v = robot.bus.read(r, m)
                vals.append(f"{r}={int(v)}")
            except Exception:
                vals.append(f"{r}=?")
        print(f"  {m:14s} " + "  ".join(vals))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--id", required=True)
    ap.add_argument("--calib", default="calib_activa_completa.json")
    ap.add_argument("--modelo", default="modelo_brazo.json")
    ap.add_argument("--vel", type=float, default=15.0, help="mm/s")
    ap.add_argument("--z", type=float, default=15.0, help="mm sobre el papel")
    ap.add_argument("--rigidez", type=int, default=16)
    ap.add_argument("--suavidad-motor", type=int, default=20)
    ap.add_argument("--hz", type=float, default=50.0)
    args = ap.parse_args()

    mapa = MapaHibrido(args.calib, args.modelo)
    y = mapa.alto / 2
    x0, x1 = mapa.ancho * 0.2, mapa.ancho * 0.8
    dur = (x1 - x0) / args.vel
    n = int(dur * args.hz)
    xs = np.linspace(x0, x1, n)
    ordenes = [mapa.articulaciones(x, y, args.z) for x in xs]

    reloj_preciso()
    robot = conectar(args.port, args.id, rigidez=args.rigidez)
    activar_par(robot)
    suavizar_motores(robot, args.suavidad_motor)
    print("\nRegistros de los motores:")
    leer_registros(robot)

    filas = []
    try:
        print("\nYendo al inicio de la línea...")
        ir_suave(robot, ordenes[0], 3.0, args.hz, vigilar=False)
        time.sleep(1.0)
        print(f"Línea de {x1 - x0:.0f} mm a {args.vel} mm/s ({dur:.0f} s)...")
        ritmo = Ritmo(args.hz)
        t0 = time.perf_counter()
        for o in ordenes:
            robot.send_action(o)
            real = leer_articulaciones(robot)
            t = time.perf_counter() - t0
            filas.append([t] + [o[k] for k in G.MOTORES] + [real[k] for k in G.MOTORES])
            ritmo.esperar()
        time.sleep(0.5)
    finally:
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()

    cab = ["t"] + [f"pedido_{k}" for k in G.MOTORES] + [f"real_{k}" for k in G.MOTORES]
    with open("diagnostico.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cab)
        w.writerows(filas)

    a = np.array(filas)
    t = a[:, 0]
    nm = len(G.MOTORES)
    print(f"\nFrecuencia real: {len(t) / t[-1]:.1f} órdenes/s (pedidas {args.hz:.0f})")
    print(f"{'motor':14s} {'recorrido':>9s} {'%parado':>8s} {'salto_med':>9s} {'salto_max':>9s} {'error_max':>9s}")
    for i, k in enumerate(G.MOTORES):
        ped, real = a[:, 1 + i], a[:, 1 + nm + i]
        recorrido = abs(ped[-1] - ped[0])
        d = np.abs(np.diff(real))
        movido = d > 0.01
        parado = 100 * (1 - movido.mean())
        salto_med = np.median(d[movido]) if movido.any() else 0.0
        salto_max = d.max()
        err = np.abs(ped - real).max()
        print(f"{k:14s} {recorrido:8.2f}° {parado:7.0f}% {salto_med:8.3f}° {salto_max:8.3f}° {err:8.2f}°")
    print("\nGuardado en diagnostico.csv")


if __name__ == "__main__":
    main()
