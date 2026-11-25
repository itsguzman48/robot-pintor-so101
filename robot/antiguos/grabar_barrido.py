"""Graba posturas mientras tú mueves el rotulador por el papel (motores sin fuerza).

Mueve el rotulador TOCANDO el papel por toda la hoja: repasa las líneas de la plantilla,
haz zigzags, círculos... Cuanto más cubras la hoja, mejor. Así el programa aprende dónde
está exactamente la superficie del papel.

Uso: python grabar_barrido.py --port COM5 --id follower_pintor
"""
import argparse
import json
import time

from robot_utils import conectar, desactivar_par, leer_articulaciones

try:
    import msvcrt
except ImportError:
    msvcrt = None


def tecla():
    if msvcrt and msvcrt.kbhit():
        msvcrt.getwch()
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--id", required=True)
    ap.add_argument("--salida", default="barrido.json")
    ap.add_argument("--hz", type=float, default=20.0, help="posturas por segundo")
    args = ap.parse_args()

    robot = conectar(args.port, args.id)
    input("\n!! SUJETA EL BRAZO: voy a quitar la fuerza a los motores. Pulsa Enter...")
    desactivar_par(robot)
    input("Apoya el rotulador en el papel y pulsa Enter para EMPEZAR a grabar...")
    print("Grabando. Mueve el rotulador por toda la hoja SIN levantarlo.")
    print("Pulsa CUALQUIER TECLA para terminar." if msvcrt else "Pulsa Ctrl+C para terminar.")
    muestras = []
    t0 = time.perf_counter()
    try:
        while not tecla():
            muestras.append(leer_articulaciones(robot))
            if len(muestras) % int(args.hz * 5) == 0:
                print(f"  {len(muestras)} posturas ({time.perf_counter() - t0:.0f} s)")
            time.sleep(1.0 / args.hz)
    except KeyboardInterrupt:
        pass
    finally:
        with open(args.salida, "w", encoding="utf-8") as f:
            json.dump({"muestras": muestras}, f)
        print(f"\nGuardadas {len(muestras)} posturas en {args.salida}")
        input("!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()


if __name__ == "__main__":
    main()
