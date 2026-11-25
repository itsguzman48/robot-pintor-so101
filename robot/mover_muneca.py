"""Gira solo la muñeca (wrist_roll) a un ángulo, para ver qué rotulador queda apuntando al papel.

El resto del brazo se queda sujeto donde está. Al pulsar Enter vuelve al ángulo de antes.

Uso:
  python mover_muneca.py --port %PORT% --id %ROBOT_ID% --angulo 84
"""
import argparse
import time

from dibujar_ik import ir_suave, reloj_preciso
from robot_utils import activar_par, conectar, leer_articulaciones, suavizar_motores


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--id", required=True)
    ap.add_argument("--angulo", type=float, required=True, help="grados del wrist_roll (rango útil -80 a 170)")
    args = ap.parse_args()
    if not -80 <= args.angulo <= 170:
        raise SystemExit("Ese ángulo está fuera del rango que medimos (-80 a 170). No lo muevo.")

    reloj_preciso()
    robot = conectar(args.port, args.id)
    actual = leer_articulaciones(robot)
    robot.send_action(actual)          # objetivo = donde está, para que no salte nada al dar fuerza
    activar_par(robot)
    suavizar_motores(robot, 20)
    antes = actual["wrist_roll.pos"]
    print(f"Muñeca ahora en {antes:.1f}°. Aparta las manos: giro a {args.angulo:.1f}°...")
    try:
        destino = dict(actual)
        destino["wrist_roll.pos"] = args.angulo
        ir_suave(robot, destino, 2.0, 50, vigilar=False)
        time.sleep(0.5)
        print(f"Muñeca en {leer_articulaciones(robot)['wrist_roll.pos']:.1f}°. Mira qué rotulador apunta al papel.")
        input("Pulsa Enter para volver al ángulo de antes...")
        ir_suave(robot, actual, 2.0, 50, vigilar=False)
    finally:
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()


if __name__ == "__main__":
    main()
