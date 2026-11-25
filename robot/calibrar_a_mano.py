"""Calibración moviendo el FOLLOWER con la mano (sin leader).

Para cada punto:
  1. El brazo está SUELTO (sin fuerza en hombro, codo y muñeca): sujétalo y lleva la punta
     del rotulador al centro de la cruz que te pide.
  2. ESPACIO: el brazo se sujeta solo en esa postura. Suéltalo con cuidado y mira la punta:
     al cargar con su peso puede ceder o moverse un poco.
  3. Si se ha movido, corrígelo con el teclado (con el brazo sujeto):
        W / S          subir / bajar
        flechas        mover en X / Y
        + / -          tamaño del paso (0.25 · 0.5 · 1 · 2 mm)
  4. ENTER: guarda el punto (la punta tiene que estar TOCANDO el centro de la cruz, sin apretar).

Otras teclas:  ESPACIO otra vez = volver a soltarlo · B = repetir el punto anterior · ESC = parar.
El giro de muñeca y la pinza NO se sueltan nunca (el rotulador no gira).

Así lo que se guarda es lo que el robot mantiene con su propia fuerza (incluye lo que cede por
su peso), que es justo lo que hay que mandarle luego para dibujar.

Uso:
  python calibrar_a_mano.py --port %PORT% --id %ROBOT_ID% --cada 2
Crea calib_activa.json (se puede parar con ESC y continuar después).
"""
import argparse
import json
import os
import time

import geometria as G
from dibujar_ik import MapaHibrido, reloj_preciso
from robot_utils import conectar, leer_articulaciones, suavizar_motores

try:
    import msvcrt
except ImportError:
    raise SystemExit("Este programa necesita Windows (lee el teclado con msvcrt).")

PASOS = [0.25, 0.5, 1.0, 2.0]
SUELTOS = [k.replace(".pos", "") for k in G.MOTORES]   # hombro, brazo, codo, muñeca (no wrist_roll ni pinza)


def leer_tecla():
    c = msvcrt.getwch()
    if c in ("\x00", "\xe0"):
        return {"H": "ARRIBA", "P": "ABAJO", "K": "IZQ", "M": "DER"}.get(msvcrt.getwch(), None)
    return c


def par(robot, activar, motores):
    """Activa o quita la fuerza solo en esos motores (si la versión de LeRobot no deja, en todos)."""
    f = robot.bus.enable_torque if activar else robot.bus.disable_torque
    try:
        f(motores)
    except TypeError:
        f()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--id", required=True)
    ap.add_argument("--calib", default="calib_limpia.json",
                    help="calibración anterior (para el giro de muñeca, la pinza y la rejilla)")
    ap.add_argument("--modelo", default="modelo_brazo.json")
    ap.add_argument("--salida", default="calib_activa.json")
    ap.add_argument("--cada", type=int, default=2,
                    help="1 de cada N puntos de la rejilla (2 = 15 puntos cada 68 mm)")
    ap.add_argument("--roll", type=float, default=None,
                    help="ángulo del giro de muñeca para ESTE rotulador (p.ej. 84 para el segundo color)")
    ap.add_argument("--filas", type=int, default=0,
                    help="usar solo las N primeras filas (p.ej. 5 = de f0 a f8 si el brazo no llega a f10)")
    ap.add_argument("--rigidez", type=int, default=16)
    ap.add_argument("--suavidad-motor", type=int, default=20)
    args = ap.parse_args()

    mapa = MapaHibrido(args.calib, args.modelo)
    t = mapa.tabla
    k = max(1, args.cada)
    cols, rows, paso_rej = (t.cols - 1) // k + 1, (t.rows - 1) // k + 1, t.paso * k
    if args.filas > 0:
        rows = min(rows, args.filas)
    fijos = {kk: v for kk, v in t.puntos[(0, 0)]["abajo"].items() if kk not in G.MOTORES}
    if args.roll is not None:
        if not -80 <= args.roll <= 170:
            raise SystemExit("--roll fuera del rango que medimos (-80 a 170).")
        fijos["wrist_roll.pos"] = args.roll
    print(f"Giro de muñeca para este rotulador: {fijos['wrist_roll.pos']:.1f}°")

    datos = {"cols": cols, "rows": rows, "paso_mm": paso_rej, "con_carga": True, "puntos": []}
    if os.path.exists(args.salida):
        with open(args.salida, encoding="utf-8") as f:
            datos = json.load(f)
        if datos["paso_mm"] != paso_rej:
            raise SystemExit(f"{args.salida} tiene puntos cada {datos['paso_mm']} mm y ahora pides cada "
                             f"{paso_rej} mm. Bórralo (del {args.salida}) o usa otro --salida.")
        print(f"Continuando: {len(datos['puntos'])} puntos ya guardados.")
    hechos = {(p["fila"], p["col"]) for p in datos["puntos"]}
    orden = []
    for f in range(rows):
        cs = range(cols) if f % 2 == 0 else reversed(range(cols))
        orden += [(f, c) for c in cs]

    def guardar():
        with open(args.salida, "w", encoding="utf-8") as fh:
            json.dump(datos, fh, indent=1)

    reloj_preciso()
    robot = conectar(args.port, args.id, rigidez=args.rigidez)
    # primero fijar giro de muñeca y pinza donde estaban en la calibración y darles fuerza
    actual = leer_articulaciones(robot)
    robot.send_action(actual)                 # objetivo = donde está: al dar fuerza no salta
    par(robot, True, None)
    suavizar_motores(robot, args.suavidad_motor)
    print("Aparta las manos: coloco el giro de muñeca de este rotulador...")
    from dibujar_ik import ir_suave
    destino = dict(actual)
    destino.update(fijos)
    ir_suave(robot, destino, 2.0, 50, vigilar=False)
    time.sleep(0.5)
    print("\n!! SUJETA EL BRAZO: en cuanto pulses Enter se queda suelto (salvo el giro de muñeca y la pinza).")
    input("Pulsa Enter cuando lo tengas sujeto...")
    par(robot, False, SUELTOS)

    i = 0
    paso = 1.0
    sujeto = False
    try:
        while i < len(orden):
            f, c = orden[i]
            if (f, c) in hechos:
                i += 1
                continue
            x0, y0 = c * paso_rej, f * paso_rej
            th = mapa.modelo.theta_en(x0, y0)
            q_ref = mapa.modelo._ik(x0, y0, 0.0, th)
            base = None          # postura en la que se sujetó con ESPACIO
            dx = dy = dz = 0.0

            def orden_actual():
                q = mapa.modelo._ik(x0 + dx, y0 + dy, dz, th)
                o = dict(base)
                for kk in G.MOTORES:
                    o[kk] = base[kk] + q[kk] - q_ref[kk]
                o.update(fijos)
                return o

            print(f"\n[{len(datos['puntos']) + 1}/{len(orden)}] Lleva la punta a c{c * 2 * k}-f{f * 2 * k} "
                  "y pulsa ESPACIO para que se sujete solo.")
            while True:
                if sujeto:
                    print(f"\r   SUJETO · ajuste x {dx:+5.2f}  y {dy:+5.2f}  altura {dz:+5.2f} mm · paso {paso} mm"
                          "   (W/S, flechas, ENTER guarda, ESPACIO suelta)   ", end="")
                tecla = leer_tecla()
                if tecla is None:
                    continue
                tl = tecla.lower() if len(tecla) == 1 else tecla
                if tecla == " ":
                    if not sujeto:
                        base = leer_articulaciones(robot)
                        base.update(fijos)
                        dx = dy = dz = 0.0
                        robot.send_action(base)          # objetivo = donde está ahora, para que no salte
                        par(robot, True, SUELTOS)
                        sujeto = True
                        print("\n   Sujeto. Suéltalo con cuidado y mira si la punta sigue tocando el centro.")
                    else:
                        par(robot, False, SUELTOS)
                        sujeto = False
                        print("\n   Suelto otra vez: sujétalo y recolócalo.")
                    continue
                if tecla == "\x1b":
                    raise KeyboardInterrupt
                if tl == "b":
                    if datos["puntos"]:
                        ult = datos["puntos"].pop()
                        hechos.discard((ult["fila"], ult["col"]))
                        guardar()
                        i = orden.index((ult["fila"], ult["col"]))
                        print("\n   Vuelvo al punto anterior.")
                        break
                    continue
                if not sujeto:
                    if tecla == "\r":
                        print("\n   Primero ESPACIO para que se sujete solo; luego ENTER.")
                    continue
                if tecla == "+":
                    paso = PASOS[min(PASOS.index(paso) + 1, len(PASOS) - 1)]
                    continue
                if tecla == "-":
                    paso = PASOS[max(PASOS.index(paso) - 1, 0)]
                    continue
                if tecla == "\r":
                    abajo = orden_actual()
                    dz0 = dz
                    dz = dz0 + 12.0
                    arriba = orden_actual()
                    dz = dz0
                    datos["puntos"].append({"fila": f, "col": c, "x_mm": x0, "y_mm": y0,
                                            "abajo": abajo, "arriba": arriba,
                                            "ajuste_mm": [dx, dy, dz0], "a_mano": True})
                    hechos.add((f, c))
                    guardar()
                    print(f"\n   OK guardado. Ahora se suelta: SUJÉTALO.")
                    # subir un poco antes de soltar, para no arrastrar la punta
                    dz = dz0 + 8.0
                    robot.send_action(orden_actual())
                    time.sleep(0.6)
                    par(robot, False, SUELTOS)
                    sujeto = False
                    i += 1
                    break
                if tecla == "DER":
                    dx += paso
                elif tecla == "IZQ":
                    dx -= paso
                elif tecla == "ABAJO":
                    dy += paso
                elif tecla == "ARRIBA":
                    dy -= paso
                elif tl == "w":
                    dz += paso
                elif tl == "s":
                    dz -= paso
                else:
                    continue
                robot.send_action(orden_actual())
        print(f"\nCalibración completa: {args.salida}")
    except KeyboardInterrupt:
        print(f"\nParado. Guardados {len(datos['puntos'])} puntos en {args.salida}; vuelve a lanzarlo para seguir.")
    finally:
        guardar()
        if not sujeto:
            print("\n(El brazo está suelto.)")
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()


if __name__ == "__main__":
    main()
