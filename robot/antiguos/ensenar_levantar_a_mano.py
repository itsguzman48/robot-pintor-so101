"""Enseñar a LEVANTAR: el robot va solo a cada punto y tú lo colocas a mano a 1 cm encima.

En cada punto:
  1. El robot va SOLO encima del punto y se queda sujeto.
  2. ENTER   -> se SUELTA. Llévalo con la mano a 1 cm JUSTO ENCIMA del centro de la cruz
               (compara con una goma o un tapón de 1 cm).
  3. ESPACIO -> se SUJETA solo. Suéltalo despacio y mira:
        - si se queda bien (1 cm encima de la cruz): ENTER -> se guarda y va solo al siguiente.
        - si no: ESPACIO -> se suelta otra vez, recolócalo y ESPACIO.

Otras teclas: B = repetir el anterior · ESC = parar (se guarda lo hecho y luego continúa).
El giro de muñeca y la pinza no se sueltan nunca.

Uso:
  python ensenar_levantar_a_mano.py --port %PORT% --id %ROBOT_ID%
Modifica calib_activa.json (antes guarda una copia en calib_activa_sin_levantar.json).
"""
import argparse
import json
import os
import shutil
import time

import geometria as G
from dibujar_ik import ir_suave, reloj_preciso
from robot_utils import activar_par, conectar, leer_articulaciones, suavizar_motores

try:
    import msvcrt
except ImportError:
    raise SystemExit("Este programa necesita Windows (lee el teclado con msvcrt).")

SUELTOS = [k.replace(".pos", "") for k in G.MOTORES]   # hombro, brazo, codo, muñeca


def par(robot, activar, motores):
    f = robot.bus.enable_torque if activar else robot.bus.disable_torque
    try:
        f(motores)
    except TypeError:
        f()


def tecla():
    k = msvcrt.getwch()
    if k in ("\x00", "\xe0"):
        msvcrt.getwch()
        return None
    return k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--id", required=True)
    ap.add_argument("--calib", default="calib_activa.json")
    ap.add_argument("--modelo", default="modelo_brazo.json")
    ap.add_argument("--altura", type=float, default=10.0, help="mm a los que dejas la punta sobre el papel")
    ap.add_argument("--rigidez", type=int, default=16)
    ap.add_argument("--suavidad-motor", type=int, default=20)
    args = ap.parse_args()

    copia = args.calib.replace(".json", "_sin_levantar.json")
    if not os.path.exists(copia):
        shutil.copyfile(args.calib, copia)
        print(f"Copia de seguridad: {copia}")
    with open(args.calib, encoding="utf-8") as f:
        d = json.load(f)
    d["altura_arriba_mm"] = args.altura
    puntos = d["puntos"]
    fijos = {k: v for k, v in puntos[0]["abajo"].items() if k not in G.MOTORES}
    M = G.Modelo(args.modelo)

    def guardar():
        with open(args.calib, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1)

    def encima(p, mm):
        """Postura aproximada a `mm` sobre el punto: tu postura tocando + lo que diga el modelo."""
        x, y = p["x_mm"], p["y_mm"]
        th = M.theta_en(x, y)
        q0, q1 = M._ik(x, y, 0.0, th), M._ik(x, y, mm, th)
        o = dict(p["abajo"])
        for k in G.MOTORES:
            o[k] = p["abajo"][k] + q1[k] - q0[k]
        o.update(fijos)
        return o

    reloj_preciso()
    robot = conectar(args.port, args.id, rigidez=args.rigidez)
    activar_par(robot)
    suavizar_motores(robot, args.suavidad_motor)
    print("\nAparta las manos: el robot va a moverse solo al primer punto.")
    input("Pulsa Enter para empezar...")
    sujeto = True

    i = 0
    try:
        while i < len(puntos):
            p = puntos[i]
            if p.get("arriba_medido"):
                i += 1
                continue
            # ir solo al punto, por arriba para no arrastrar
            if not sujeto:
                actual = leer_articulaciones(robot)
                actual.update(fijos)
                robot.send_action(actual)
                par(robot, True, SUELTOS)
                sujeto = True
            try:
                ir_suave(robot, encima(p, args.altura + 20), 2.0, 50, vigilar=False)
                ir_suave(robot, encima(p, args.altura), 1.0, 50, vigilar=False)
            except ValueError:
                print("   (el modelo no llega encima de este punto: llévalo tú a mano)")
            hechos = sum(1 for q in puntos if q.get("arriba_medido"))
            print(f"\n[{hechos + 1}/{len(puntos)}] Punto c{round(p['x_mm'] / 17)}-f{round(p['y_mm'] / 17)}. "
                  "ENTER para soltarlo y colocarlo a 1 cm encima de la cruz.")
            fase = "llegado"        # llegado -> suelto -> colocado
            postura = None
            while True:
                k = tecla()
                if k is None:
                    continue
                if k == "\x1b":
                    raise KeyboardInterrupt
                if k.lower() == "b":
                    previos = [j for j in range(i) if puntos[j].get("arriba_medido")]
                    if previos:
                        puntos[previos[-1]]["arriba_medido"] = False
                        guardar()
                        i = previos[-1]
                        print("   Vuelvo al punto anterior.")
                        break
                    continue
                if fase == "llegado" and k == "\r":
                    par(robot, False, SUELTOS)
                    sujeto = False
                    fase = "suelto"
                    print("   SUELTO: llévalo a 1 cm encima del centro de la cruz y pulsa ESPACIO.")
                elif fase == "suelto" and k == " ":
                    postura = leer_articulaciones(robot)
                    postura.update(fijos)
                    robot.send_action(postura)
                    par(robot, True, SUELTOS)
                    sujeto = True
                    fase = "colocado"
                    print("   SUJETO: suéltalo despacio. ¿Se queda bien? ENTER = guardar · ESPACIO = recolocar")
                elif fase == "colocado" and k == " ":
                    par(robot, False, SUELTOS)
                    sujeto = False
                    fase = "suelto"
                    print("   SUELTO otra vez: recolócalo y ESPACIO.")
                elif fase == "colocado" and k == "\r":
                    p["arriba"] = dict(postura)
                    p["arriba_medido"] = True
                    guardar()
                    print("   OK guardado. Voy al siguiente...")
                    i += 1
                    break
        print(f"\nListo: los {len(puntos)} puntos saben levantar.")
        try:
            ir_suave(robot, encima(puntos[-1], args.altura + 20), 1.5, 50, vigilar=False)
        except ValueError:
            pass
    except KeyboardInterrupt:
        hechos = sum(1 for q in puntos if q.get("arriba_medido"))
        print(f"\nParado. {hechos}/{len(puntos)} puntos; vuelve a lanzarlo para seguir.")
    finally:
        guardar()
        if not sujeto:
            print("(El brazo está suelto.)")
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()


if __name__ == "__main__":
    main()
