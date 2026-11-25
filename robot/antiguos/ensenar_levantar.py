"""Enseñar al robot qué es LEVANTAR el rotulador en cada zona de la hoja.

Usa los puntos que ya calibraste (calib_activa.json, rotulador tocando). En cada punto el robot
se coloca más o menos encima; tú lo dejas con el teclado JUSTO ENCIMA del centro de la cruz,
a la altura de referencia (10 mm por defecto) y pulsas ENTER.

Así, al dibujar, bajar y despegar (los últimos 10 mm) es ir en línea recta entre tu postura
"tocando" y tu postura "encima": sube y baja en vertical de verdad y no mancha.

Truco para la altura: pon al lado de la cruz algo de 10 mm de alto (una goma, un tapón, dos
monedas apiladas...) y mira de lado; la punta tiene que quedar a esa altura y vertical sobre la cruz.

Teclas (el robot se sujeta solo todo el rato):
    flechas    mover en X / Y            W / S   subir / bajar
    + / -      tamaño del paso (0.25 · 0.5 · 1 · 2 mm)
    ENTER      guardar este punto        B       repetir el anterior        ESC   parar

Uso:
  python ensenar_levantar.py --port %PORT% --id %ROBOT_ID%
Modifica calib_activa.json (antes guarda una copia en calib_activa_sin_levantar.json).
Se puede parar con ESC y continuar después.
"""
import argparse
import json
import os
import shutil
import time

import geometria as G
from dibujar_ik import ir_suave, reloj_preciso
from robot_utils import activar_par, conectar, suavizar_motores

try:
    import msvcrt
except ImportError:
    raise SystemExit("Este programa necesita Windows (lee el teclado con msvcrt).")

PASOS = [0.25, 0.5, 1.0, 2.0]


def leer_tecla():
    c = msvcrt.getwch()
    if c in ("\x00", "\xe0"):
        return {"H": "ARRIBA", "P": "ABAJO", "K": "IZQ", "M": "DER"}.get(msvcrt.getwch(), None)
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--id", required=True)
    ap.add_argument("--calib", default="calib_activa.json")
    ap.add_argument("--modelo", default="modelo_brazo.json")
    ap.add_argument("--altura", type=float, default=10.0, help="mm de la altura de referencia")
    ap.add_argument("--rigidez", type=int, default=16)
    ap.add_argument("--suavidad-motor", type=int, default=20)
    args = ap.parse_args()

    copia = args.calib.replace(".json", "_sin_levantar.json")
    if not os.path.exists(copia):
        shutil.copyfile(args.calib, copia)
        print(f"Copia de seguridad: {copia}")
    with open(args.calib, encoding="utf-8") as f:
        d = json.load(f)
    if d.get("altura_arriba_mm") not in (None, args.altura) and any(p.get("arriba_medido") for p in d["puntos"]):
        raise SystemExit(f"Ya hay puntos enseñados a {d['altura_arriba_mm']} mm. Usa --altura {d['altura_arriba_mm']}.")
    d["altura_arriba_mm"] = args.altura
    M = G.Modelo(args.modelo)
    puntos = d["puntos"]

    def guardar():
        with open(args.calib, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1)

    def mover_con_modelo(base, x, y, dx, dy, dz):
        """base (postura tocando en x, y) + lo que dice el modelo para desplazarse dx, dy, dz."""
        th = M.theta_en(x, y)
        q0 = M._ik(x, y, 0.0, th)
        q1 = M._ik(x + dx, y + dy, dz, th)
        o = dict(base)
        for k in G.MOTORES:
            o[k] = base[k] + q1[k] - q0[k]
        return o

    reloj_preciso()
    robot = conectar(args.port, args.id, rigidez=args.rigidez)
    activar_par(robot)
    suavizar_motores(robot, args.suavidad_motor)
    paso = 0.5
    i = 0
    try:
        while i < len(puntos):
            p = puntos[i]
            if p.get("arriba_medido"):
                i += 1
                continue
            x0, y0 = p["x_mm"], p["y_mm"]
            abajo = p["abajo"]
            dx = dy = 0.0
            dz = args.altura
            # ir por arriba hasta el punto (sin arrastrar) y bajar a la altura de referencia
            try:
                ir_suave(robot, mover_con_modelo(abajo, x0, y0, 0, 0, args.altura + 25), 2.0, 50, vigilar=False)
            except ValueError:
                pass
            ir_suave(robot, mover_con_modelo(abajo, x0, y0, dx, dy, dz), 1.2, 50, vigilar=False)
            hechos = sum(1 for q in puntos if q.get("arriba_medido"))
            print(f"\n[{hechos + 1}/{len(puntos)}] Punto c{round(x0 / 17)}-f{round(y0 / 17)}: deja la punta JUSTO "
                  f"ENCIMA del centro de la cruz, a {args.altura:.0f} mm, y ENTER.")
            while True:
                print(f"\r   ajuste x {dx:+5.2f}  y {dy:+5.2f}  altura (según modelo) {dz:5.2f} mm   paso {paso} mm   ",
                      end="")
                k = leer_tecla()
                if k is None:
                    continue
                kl = k.lower() if len(k) == 1 else k
                if k == "\x1b":
                    raise KeyboardInterrupt
                if k == "+":
                    paso = PASOS[min(PASOS.index(paso) + 1, len(PASOS) - 1)]
                    continue
                if k == "-":
                    paso = PASOS[max(PASOS.index(paso) - 1, 0)]
                    continue
                if kl == "b":
                    previos = [j for j in range(i) if puntos[j].get("arriba_medido")]
                    if previos:
                        j = previos[-1]
                        puntos[j]["arriba_medido"] = False
                        guardar()
                        i = j
                        print("\n   Vuelvo al punto anterior.")
                        break
                    continue
                if k == "\r":
                    p["arriba"] = mover_con_modelo(abajo, x0, y0, dx, dy, dz)
                    p["arriba_medido"] = True
                    p["arriba_ajuste_mm"] = [dx, dy, dz]
                    guardar()
                    print(f"\n   OK guardado (ajuste x {dx:+.1f}, y {dy:+.1f}; el modelo creía {dz:.1f} mm)")
                    i += 1
                    break
                if k == "DER":
                    dx += paso
                elif k == "IZQ":
                    dx -= paso
                elif k == "ABAJO":
                    dy += paso
                elif k == "ARRIBA":
                    dy -= paso
                elif kl == "w":
                    dz += paso
                elif kl == "s":
                    dz -= paso
                else:
                    continue
                try:
                    robot.send_action(mover_con_modelo(abajo, x0, y0, dx, dy, dz))
                except ValueError:
                    print("\n   No llego ahí.")
        print(f"\nListo: {args.calib} ya sabe levantar en los {len(puntos)} puntos.")
        try:
            ultimo = puntos[-1]
            ir_suave(robot, mover_con_modelo(ultimo["abajo"], ultimo["x_mm"], ultimo["y_mm"], 0, 0,
                                             args.altura + 25), 1.5, 50, vigilar=False)
        except ValueError:
            pass
    except KeyboardInterrupt:
        hechos = sum(1 for q in puntos if q.get("arriba_medido"))
        print(f"\nParado. {hechos}/{len(puntos)} puntos enseñados; vuelve a lanzarlo para seguir.")
    finally:
        guardar()
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()


if __name__ == "__main__":
    main()
