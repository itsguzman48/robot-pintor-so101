"""Nivelar A MANO: le enseñas en 5 puntos cómo es "tocar justo" y "rotulador perpendicular".

En cada punto:
  1. El robot va SOLO encima del punto (unos 15 mm) y se queda sujeto.
  2. ENTER   -> se SUELTA. Con la mano, deja la punta sobre el centro de la cruz:
                ROZANDO el papel (que no se doble) y con el rotulador PERPENDICULAR.
  3. ESPACIO -> se SUJETA solo. Suéltalo despacio y mira:
        - si sigue rozando y recto: ENTER -> se guarda y va solo al siguiente.
        - si no: ESPACIO -> se suelta, recolócalo y ESPACIO.
  B = repetir el anterior · ESC = cancelar sin guardar.
  El giro de muñeca y la pinza no se sueltan nunca.

Con los 5 puntos:
  - Inclinación: todos los puntos de la calibración pasan a tener la inclinación media que le has
    enseñado (la punta sigue en el mismo sitio).
  - Altura: guarda un plano con la altura a la que toca de verdad. Con eso --z-pintar 0 = rozando.

Uso (una vez por rotulador, con el servidor PARADO):
  python nivelar.py --port %PORT% --id %ROBOT_ID% --calib calib_color1.json --modelo modelo_color1.json
  python nivelar.py --port %PORT% --id %ROBOT_ID% --calib calib_color2.json --modelo modelo_color2.json
Copias de seguridad: <calib>_antes_nivel.json y <modelo>_antes_nivel.json
"""
import argparse
import json
import math
import shutil

import numpy as np

import geometria as G
from dibujar_ik import MapaHibrido, ir_suave, reloj_preciso
from robot_utils import activar_par, conectar, leer_articulaciones, suavizar_motores

try:
    import msvcrt
except ImportError:
    raise SystemExit("Este programa necesita Windows (lee el teclado con msvcrt).")

PUNTOS = [(136, 68), (34, 34), (238, 34), (238, 102), (34, 102)]
SUELTOS = [k.replace(".pos", "") for k in G.MOTORES]


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
    ap.add_argument("--calib", required=True)
    ap.add_argument("--modelo", required=True)
    ap.add_argument("--rigidez", type=int, default=16)
    args = ap.parse_args()

    with open(args.calib, encoding="utf-8") as f:
        d = json.load(f)
    d.pop("nivel", None)                                  # medir respecto a la calibración pura
    tmp = args.calib.replace(".json", "_sin_nivel.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    mapa = MapaHibrido(tmp, args.modelo, subida="hombro")
    M = mapa.modelo
    fijos = {k: v for k, v in d["puntos"][0]["abajo"].items() if k not in G.MOTORES}

    reloj_preciso()
    robot = conectar(args.port, args.id, rigidez=args.rigidez)
    robot.send_action(leer_articulaciones(robot))
    activar_par(robot)
    suavizar_motores(robot, 20)
    print("Aparta las manos: el robot va a moverse solo al primer punto.")
    input("Pulsa Enter para empezar...")

    posturas = {}
    sujeto = True
    i = 0
    try:
        while i < len(PUNTOS):
            x, y = PUNTOS[i]
            if not sujeto:
                a = leer_articulaciones(robot)
                a.update(fijos)
                robot.send_action(a)
                par(robot, True, SUELTOS)
                sujeto = True
            ir_suave(robot, mapa.articulaciones(x, y, 40), 2.5, 50, vigilar=False)
            ir_suave(robot, mapa.articulaciones(x, y, 15), 1.2, 50, vigilar=False)
            print(f"\n[{i + 1}/{len(PUNTOS)}] Punto ({x}, {y}) mm. ENTER para soltarlo; ponlo ROZANDO la cruz "
                  "y PERPENDICULAR; luego ESPACIO.")
            fase, postura = "llegado", None
            while True:
                k = tecla()
                if k is None:
                    continue
                if k == "\x1b":
                    raise KeyboardInterrupt
                if k.lower() == "b" and i > 0:
                    i -= 1
                    posturas.pop(PUNTOS[i], None)
                    print("   Vuelvo al punto anterior.")
                    break
                if fase == "llegado" and k == "\r":
                    par(robot, False, SUELTOS)
                    sujeto, fase = False, "suelto"
                    print("   SUELTO: punta rozando el centro de la cruz, rotulador recto, y ESPACIO.")
                elif fase == "suelto" and k == " ":
                    postura = leer_articulaciones(robot)
                    postura.update(fijos)
                    robot.send_action(postura)
                    par(robot, True, SUELTOS)
                    sujeto, fase = True, "colocado"
                    print("   SUJETO: suéltalo despacio. ¿Sigue rozando y recto? ENTER = guardar · ESPACIO = recolocar")
                elif fase == "colocado" and k == " ":
                    par(robot, False, SUELTOS)
                    sujeto, fase = False, "suelto"
                    print("   SUELTO otra vez: recolócalo y ESPACIO.")
                elif fase == "colocado" and k == "\r":
                    posturas[(x, y)] = postura
                    print("   OK guardado.")
                    ir_suave(robot, mapa._subir_con_hombro(postura, 15), 0.8, 50, vigilar=False)
                    i += 1
                    break
        ir_suave(robot, mapa.articulaciones(136, 68, 40), 2.0, 50, vigilar=False)
    except KeyboardInterrupt:
        print("\nCancelado: no guardo nada.")
        posturas = {}
    finally:
        if not sujeto:
            print("(El brazo está suelto.)")
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()

    if len(posturas) < len(PUNTOS):
        return
    procesar(args, mapa, posturas)


def procesar(args, mapa, posturas):
    M = mapa.modelo

    def vec(q):
        return np.array([[q[k] for k in G.MOTORES]])

    # 1) inclinación que le has enseñado
    angs = [float(G.angulo_pinza(vec(q), M.p, M.s)[0]) for q in posturas.values()]
    th = float(np.mean(angs))
    print(f"\nInclinación enseñada: media {math.degrees(th):.1f}° "
          f"(entre {math.degrees(min(angs)):.1f}° y {math.degrees(max(angs)):.1f}°)")

    # 2) altura de contacto: punta de tu postura menos punta de la calibración, en el mismo modelo
    medidos = []
    for (x, y), q in posturas.items():
        z_tuya = G.fk(vec(q), M.p, M.s)[0][2]
        z_cal = G.fk(vec(mapa.articulaciones(x, y, 0)), M.p, M.s)[0][2]
        medidos.append((x, y, float(z_tuya - z_cal)))
    m = np.array(medidos)
    A = np.c_[np.ones(len(m)), m[:, 0], m[:, 1]]
    plano = np.linalg.lstsq(A, m[:, 2], rcond=None)[0]
    print("Altura a la que toca de verdad (mm respecto a la calibración):")
    for x, y, z in medidos:
        print(f"  ({x:3.0f}, {y:3.0f}): {z:+5.1f}")

    # 3) aplicar la inclinación a todos los puntos (la punta se queda donde está)
    with open(args.calib, encoding="utf-8") as f:
        d = json.load(f)
    no_llega = []
    for p in d["puntos"]:
        x, y = p["x_mm"], p["y_mm"]
        for capa, z in (("abajo", 0.0), ("arriba", 12.0)):
            q = p[capa]
            th_q = float(G.angulo_pinza(vec(q), M.p, M.s)[0])
            try:
                viejo, nuevo = M._ik(x, y, z, th_q), M._ik(x, y, z, th)
            except ValueError:
                if capa == "abajo":
                    no_llega.append(f"c{round(x / 17)}-f{round(y / 17)}")
                continue
            for k in G.MOTORES:
                q[k] = q[k] + nuevo[k] - viejo[k]
    d["nivel"] = {"puntos": m.tolist(), "plano": [float(v) for v in plano], "inclinacion_rad": th}
    shutil.copyfile(args.calib, args.calib.replace(".json", "_antes_nivel.json"))
    shutil.copyfile(args.modelo, args.modelo.replace(".json", "_antes_nivel.json"))
    with open(args.calib, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    with open(args.modelo, encoding="utf-8") as f:
        mod = json.load(f)
    mod["angulo_pinza"] = th
    mod["angulo_pinza_coef"] = [th, 0, 0, 0, 0, 0]
    with open(args.modelo, "w", encoding="utf-8") as f:
        json.dump(mod, f, indent=2)
    if no_llega:
        print(f"[aviso] Con esa inclinación el modelo no llega a {', '.join(no_llega)} (se quedan como estaban).")
    print(f"\nGuardado {args.calib} y {args.modelo}. Ahora --z-pintar 0 = rozando.")


if __name__ == "__main__":
    main()
