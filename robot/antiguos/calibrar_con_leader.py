"""Calibración moviendo el robot CON LA MANO a través del brazo leader.

El follower (el que lleva el rotulador) se sujeta solo con sus motores y copia lo que haces con el
leader. Así lo mueves tú a mano, pero lo que se guarda ya incluye lo que cede por su peso.
El giro de muñeca y la pinza del follower se quedan fijos (el rotulador no se mueve).

Teclas (con la ventana de la terminal seleccionada):
    ENTER   guardar el punto que te pide (rotulador TOCANDO el centro de la cruz)
    B       repetir el punto anterior
    ESC     parar y guardar lo hecho (al relanzarlo continúa)

Uso:
  python calibrar_con_leader.py --port COM5 --id follower_pintor --leader-port COM4 --leader-id leader_pintor
Crea calib_activa.json (el mismo archivo que calibrar_activo.py).
"""
import argparse
import importlib
import json
import os
import pkgutil
import time

import geometria as G
from dibujar_ik import MapaHibrido, Ritmo, reloj_preciso
from robot_utils import activar_par, conectar, leer_articulaciones, suavizar_motores

try:
    import msvcrt
except ImportError:
    raise SystemExit("Este programa necesita Windows (lee el teclado con msvcrt).")


def conectar_leader(port, leader_id):
    """Busca la clase del brazo leader en la versión de LeRobot instalada."""
    import lerobot.teleoperators as T
    errores = []
    for m in pkgutil.iter_modules(T.__path__):
        if "leader" not in m.name or "so" not in m.name:
            continue
        try:
            mod = importlib.import_module(f"lerobot.teleoperators.{m.name}")
        except Exception as e:
            errores.append(f"{m.name}: {e!r}")
            continue
        clases = [getattr(mod, n) for n in dir(mod) if n.endswith("Leader") and isinstance(getattr(mod, n), type)]
        configs = [getattr(mod, n) for n in dir(mod) if "Leader" in n and n.endswith("Config")
                   and isinstance(getattr(mod, n), type)]
        for Cl in clases:
            for Cf in configs:
                for extra in ({}, {"leader_arm_type": "so101"}, {"teleop_type": "so101_leader"}):
                    try:
                        tele = Cl(Cf(port=port, id=leader_id, **extra))
                        tele.connect()
                        return tele
                    except Exception as e:
                        errores.append(f"{m.name}.{Cl.__name__}: {e!r}")
    raise SystemExit("No he podido conectar con el leader:\n" + "\n".join(errores[-6:]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5", help="puerto del follower")
    ap.add_argument("--id", required=True, help="id del follower")
    ap.add_argument("--leader-port", required=True)
    ap.add_argument("--leader-id", default="leader_pintor")
    ap.add_argument("--calib", default="calib_limpia.json", help="calibración anterior (para el giro de muñeca y la pinza)")
    ap.add_argument("--modelo", default="modelo_brazo.json")
    ap.add_argument("--salida", default="calib_activa.json")
    ap.add_argument("--rigidez", type=int, default=16)
    ap.add_argument("--suavidad-motor", type=int, default=20)
    ap.add_argument("--cada", type=int, default=1,
                    help="usar 1 de cada N puntos de la rejilla (2 = 15 puntos cada 68 mm, mucho más rápido)")
    args = ap.parse_args()

    mapa = MapaHibrido(args.calib, args.modelo)
    t = mapa.tabla
    fijos = {k: v for k, v in t.puntos[(0, 0)]["abajo"].items() if k not in G.MOTORES}
    k = max(1, args.cada)
    cols, rows, paso = (t.cols - 1) // k + 1, (t.rows - 1) // k + 1, t.paso * k
    datos = {"cols": cols, "rows": rows, "paso_mm": paso, "con_carga": True, "puntos": []}
    if os.path.exists(args.salida):
        with open(args.salida, encoding="utf-8") as f:
            datos = json.load(f)
        if datos["paso_mm"] != paso:
            raise SystemExit(f"{args.salida} ya tiene puntos cada {datos['paso_mm']} mm y ahora pides cada {paso} mm. "
                             f"Bórralo (del {args.salida}) o usa otro --salida.")
        print(f"Continuando: {len(datos['puntos'])} puntos ya guardados.")
    hechos = {(p["fila"], p["col"]) for p in datos["puntos"]}
    orden = []
    for f in range(rows):
        cs = range(cols) if f % 2 == 0 else reversed(range(cols))
        orden += [(f, c) for c in cs]

    def guardar():
        with open(args.salida, "w", encoding="utf-8") as fh:
            json.dump(datos, fh, indent=1)

    def siguiente():
        for (f, c) in orden:
            if (f, c) not in hechos:
                return f, c
        return None

    reloj_preciso()
    leader = conectar_leader(args.leader_port, args.leader_id)
    robot = conectar(args.port, args.id, rigidez=args.rigidez)
    activar_par(robot)
    suavizar_motores(robot, args.suavidad_motor)

    def orden_desde_leader():
        a = leader.get_action()
        o = {k: float(a[k]) for k in G.MOTORES}
        o.update(fijos)
        return o

    # entrar suave: del sitio actual a donde está el leader
    print("\nPon el leader más o menos en la misma postura que el follower.")
    input("Pulsa Enter y el follower se irá despacio a la postura del leader...")
    origen = leer_articulaciones(robot)
    ritmo = Ritmo(50)
    for i in range(1, 151):
        objetivo = orden_desde_leader()
        s = i / 150
        s = s * s * (3 - 2 * s)
        robot.send_action({k: origen[k] + (objetivo[k] - origen[k]) * s for k in objetivo})
        ritmo.esperar()

    try:
        actual = siguiente()
        while actual:
            f, c = actual
            print(f"\n[{len(datos['puntos']) + 1}/{len(orden)}] Lleva el rotulador a c{c * 2 * k}-f{f * 2 * k} "
                  "tocando el centro de la cruz y pulsa ENTER.")
            while True:
                o = orden_desde_leader()
                robot.send_action(o)
                ritmo.esperar()
                if not msvcrt.kbhit():
                    continue
                k = msvcrt.getwch()
                if k == "\r":
                    abajo = dict(o)
                    x0, y0 = c * paso, f * paso
                    th = mapa.modelo.theta_en(x0, y0)
                    q0, q1 = mapa.modelo._ik(x0, y0, 0.0, th), mapa.modelo._ik(x0, y0, 12.0, th)
                    arriba = dict(abajo)
                    for kk in G.MOTORES:
                        arriba[kk] = abajo[kk] + q1[kk] - q0[kk]
                    datos["puntos"].append({"fila": f, "col": c, "x_mm": x0, "y_mm": y0,
                                            "abajo": abajo, "arriba": arriba})
                    hechos.add((f, c))
                    guardar()
                    print("   OK guardado")
                    break
                if k.lower() == "b" and datos["puntos"]:
                    ult = datos["puntos"].pop()
                    hechos.discard((ult["fila"], ult["col"]))
                    guardar()
                    print("   Vuelvo al punto anterior.")
                    break
                if k == "\x1b":
                    raise KeyboardInterrupt
            actual = siguiente()
        print(f"\nCalibración completa: {args.salida}")
    except KeyboardInterrupt:
        print(f"\nParado. Guardados {len(datos['puntos'])} puntos en {args.salida}; vuelve a lanzarlo para seguir.")
    finally:
        guardar()
        input("\n!! SUJETA EL FOLLOWER: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()
        try:
            leader.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
