"""Calibración con el robot SUJETÁNDOSE SOLO (motores con fuerza).

Así lo que se guarda ya incluye lo que el brazo cede por su peso: es justo la orden que hay que
mandarle para que el rotulador toque cada punto de verdad.

Para cada punto, el robot se coloca él solo un poco por encima. Tú lo afinas con el teclado:
    Flechas izquierda / derecha     mover en X (columnas)
    Flechas arriba / abajo          mover en Y (filas)
    W / S                           subir / bajar el rotulador
    Mayúsculas (W S y flechas no)   -> usa  A / D  para pasos grandes en X,  Q / E para Y
    + / -                           cambiar el tamaño del paso (0.25 · 0.5 · 1 · 2 mm)
    ENTER                           guardar el punto (el rotulador tiene que estar TOCANDO el centro)
    B                               volver al punto anterior
    ESC                             parar y guardar lo hecho

Uso: python calibrar_activo.py --port COM5 --id follower_pintor
Crea calib_activa.json (se puede parar y continuar: retoma donde lo dejaste).
"""
import argparse
import json
import os
import time

import geometria as G
from dibujar_ik import MapaHibrido, ir_suave, reloj_preciso
from robot_utils import activar_par, conectar, leer_articulaciones, suavizar_motores

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
    ap.add_argument("--calib", default="calib_limpia.json", help="calibración anterior (para la primera posición)")
    ap.add_argument("--modelo", default="modelo_brazo.json")
    ap.add_argument("--salida", default="calib_activa.json")
    ap.add_argument("--altura-inicial", type=float, default=20.0, help="mm por encima a los que se coloca")
    ap.add_argument("--rigidez", type=int, default=16)
    ap.add_argument("--suavidad-motor", type=int, default=20)
    args = ap.parse_args()

    mapa = MapaHibrido(args.calib, args.modelo)
    t = mapa.tabla
    datos = {"cols": t.cols, "rows": t.rows, "paso_mm": t.paso, "con_carga": True, "puntos": []}
    if os.path.exists(args.salida):
        with open(args.salida, encoding="utf-8") as f:
            datos = json.load(f)
        print(f"Continuando: {len(datos['puntos'])} puntos ya guardados.")
    hechos = {(p["fila"], p["col"]) for p in datos["puntos"]}

    orden = []
    for f in range(t.rows):
        cs = range(t.cols) if f % 2 == 0 else reversed(range(t.cols))
        orden += [(f, c) for c in cs]

    reloj_preciso()
    robot = conectar(args.port, args.id, rigidez=args.rigidez)
    activar_par(robot)
    suavizar_motores(robot, args.suavidad_motor)
    fijos = {k: v for k, v in t.puntos[(0, 0)]["abajo"].items() if k not in G.MOTORES}

    def guardar():
        with open(args.salida, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=1)

    i = 0
    paso = 1.0
    primero = True
    try:
        while i < len(orden):
            f, c = orden[i]
            if (f, c) in hechos:
                i += 1
                continue
            x0, y0 = c * t.paso, f * t.paso
            dx = dy = 0.0
            z = args.altura_inicial

            base_nodo = mapa.articulaciones(x0, y0, 0.0)   # posición propuesta sobre el punto
            th = mapa.modelo.theta_en(x0, y0)
            q_ref = mapa.modelo._ik(x0, y0, 0.0, th)

            def orden_actual():
                # los ajustes se hacen con el modelo del brazo (sirve también fuera de la hoja)
                q = mapa.modelo._ik(x0 + dx, y0 + dy, z, th)
                o = dict(base_nodo)
                for k in G.MOTORES:
                    o[k] = base_nodo[k] + q[k] - q_ref[k]
                o.update(fijos)
                return o

            ir_suave(robot, orden_actual(), 3.0 if primero else 1.5, 50, vigilar=False)
            primero = False
            hechas = len(datos["puntos"])
            print(f"\n[{hechas + 1}/{len(orden)}] Punto c{round(x0 / 17)}-f{round(y0 / 17)}. "
                  "Centra con flechas, baja con S hasta que TOQUE, y ENTER.")
            while True:
                print(f"\r   ajuste x {dx:+5.2f}  y {dy:+5.2f}  altura {z:+5.2f} mm   paso {paso} mm   ", end="")
                k = leer_tecla()
                if k is None:
                    continue
                kl = k.lower() if isinstance(k, str) and len(k) == 1 else k
                if k == "DER":
                    dx += paso
                elif k == "IZQ":
                    dx -= paso
                elif k == "ABAJO":
                    dy += paso
                elif k == "ARRIBA":
                    dy -= paso
                elif kl == "d":
                    dx += 3
                elif kl == "a":
                    dx -= 3
                elif kl == "e":
                    dy += 3
                elif kl == "q":
                    dy -= 3
                elif kl == "w":
                    z += paso
                elif kl == "s":
                    z -= paso
                elif k == "+":
                    paso = PASOS[min(PASOS.index(paso) + 1, len(PASOS) - 1)]
                    continue
                elif k == "-":
                    paso = PASOS[max(PASOS.index(paso) - 1, 0)]
                    continue
                elif k == "\r":
                    abajo = orden_actual()
                    z_guardada = z
                    z = z_guardada + 12.0
                    arriba = orden_actual()
                    z = z_guardada
                    datos["puntos"].append({"fila": f, "col": c, "x_mm": x0, "y_mm": y0,
                                            "abajo": abajo, "arriba": arriba,
                                            "ajuste_mm": [dx, dy, z_guardada]})
                    hechos.add((f, c))
                    guardar()
                    print(f"\n   OK guardado (ajuste x {dx:+.1f}, y {dy:+.1f}, altura {z_guardada:+.1f} mm)")
                    z = z_guardada + 8.0
                    robot.send_action(orden_actual())
                    time.sleep(0.4)
                    i += 1
                    break
                elif kl == "b":
                    if datos["puntos"]:
                        ult = datos["puntos"].pop()
                        hechos.discard((ult["fila"], ult["col"]))
                        guardar()
                        i = orden.index((ult["fila"], ult["col"]))
                        print("\n   Vuelvo al punto anterior.")
                        break
                    continue
                elif k == "\x1b":
                    raise KeyboardInterrupt
                else:
                    continue
                robot.send_action(orden_actual())
        print(f"\nCalibración completa: {args.salida}")
    except KeyboardInterrupt:
        print(f"\nParado. Guardados {len(datos['puntos'])} puntos en {args.salida}; vuelve a lanzarlo para seguir.")
    finally:
        guardar()
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()


if __name__ == "__main__":
    main()
