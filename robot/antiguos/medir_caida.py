"""Mide cuánto cede el brazo por su peso en cada zona de la hoja y lo guarda para corregirlo.

Cuando calibraste, los motores estaban sin fuerza y el brazo estaba exactamente donde lo pusiste.
Cuando el robot se sujeta solo, los motores ceden un poco por el peso, más cuanto más estirado
está el brazo. Este programa lleva el rotulador ENCIMA de cada punto de la cuadrícula (sin tocar el
papel), espera a que se quede quieto, lee dónde está de verdad y guarda la diferencia.
dibujar_ik.py usa esa corrección automáticamente (archivo correccion.json).

Uso:
  python medir_caida.py --port COM5 --id follower_pintor
  (repítelo una segunda vez para afinar: parte de la corrección anterior y la mejora)
"""
import argparse
import json
import os
import time

import numpy as np

import geometria as G
from dibujar_ik import MapaHibrido, Ritmo, ir_suave, reloj_preciso, vaciar_teclado, comprobar_parada, ParadaEmergencia
from robot_utils import activar_par, conectar, leer_articulaciones, suavizar_motores

LIMITE = 10.0  # grados: corrección máxima permitida por motor


def leer_media(robot, n=5):
    acum = None
    for _ in range(n):
        o = leer_articulaciones(robot)
        acum = o if acum is None else {k: acum[k] + o[k] for k in acum}
        time.sleep(0.02)
    return {k: v / n for k, v in acum.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--id", required=True)
    ap.add_argument("--calib", default="calib_limpia.json")
    ap.add_argument("--modelo", default="modelo_brazo.json")
    ap.add_argument("--correccion", default="correccion.json")
    ap.add_argument("--z", type=float, default=30.0, help="mm sobre el papel a los que mide")
    ap.add_argument("--rigidez", type=int, default=16)
    ap.add_argument("--suavidad-motor", type=int, default=20)
    ap.add_argument("--espera", type=float, default=1.0, help="segundos quieto antes de medir")
    args = ap.parse_args()

    mapa = MapaHibrido(args.calib, args.modelo)  # ya aplica correccion.json si existe
    tabla = mapa.tabla
    previa = mapa.correccion
    filas, cols = tabla.rows, tabla.cols
    print("Corrección anterior: " + ("sí, la mejoro" if previa else "no hay, empiezo de cero"))

    reloj_preciso()
    robot = conectar(args.port, args.id, rigidez=args.rigidez)
    activar_par(robot)
    suavizar_motores(robot, args.suavidad_motor)
    print(f"\nVa a recorrer {filas * cols} puntos a {args.z:.0f} mm del papel (unos {filas * cols * (args.espera + 1.2) / 60:.0f} min).")
    print("PARADA DE EMERGENCIA: cualquier tecla.")
    for s in (3, 2, 1):
        print(f"Empieza en {s}...")
        time.sleep(1)
    vaciar_teclado()

    nueva = {}
    try:
        primero = True
        for f in range(filas):
            orden = range(cols) if f % 2 == 0 else reversed(range(cols))
            for c in orden:
                x, y = c * tabla.paso, f * tabla.paso
                deseado = mapa.articulaciones(x, y, args.z, corregir=False)
                mandado = mapa.articulaciones(x, y, args.z)
                ir_suave(robot, mandado, 3.0 if primero else 1.2, 50)
                primero = False
                t0 = time.perf_counter()
                while time.perf_counter() - t0 < args.espera:
                    comprobar_parada()
                    time.sleep(0.02)
                real = leer_media(robot)
                corr_ant = previa.get(f"{f},{c}", {k: 0.0 for k in G.MOTORES}) if previa else {k: 0.0 for k in G.MOTORES}
                corr = {k: float(np.clip(corr_ant[k] + (deseado[k] - real[k]), -LIMITE, LIMITE)) for k in G.MOTORES}
                nueva[f"{f},{c}"] = corr
                err = "  ".join(f"{k.replace('.pos', '')} {deseado[k] - real[k]:+.1f}°" for k in G.MOTORES)
                print(f"  c{round(x / 17)}-f{round(y / 17)}: diferencia {err}")
        ir_suave(robot, mapa.articulaciones(x, y, args.z + 20), 2.0, 50)
        print("\nMedición terminada.")
    except (ParadaEmergencia, KeyboardInterrupt):
        robot.send_action(leer_articulaciones(robot))
        print("\n!!! PARADA: guardo lo medido hasta ahora (los puntos que faltan conservan la corrección anterior).")
    finally:
        if nueva:
            completa = dict(previa or {})
            completa.update(nueva)
            for f in range(filas):
                for c in range(cols):
                    completa.setdefault(f"{f},{c}", {k: 0.0 for k in G.MOTORES})
            with open(args.correccion, "w", encoding="utf-8") as fh:
                json.dump({"filas": filas, "cols": cols, "paso_mm": tabla.paso, "valores": completa}, fh, indent=1)
            todos = np.array([[v[k] for k in G.MOTORES] for v in completa.values()])
            print(f"Guardado {args.correccion}. Corrección media por motor (grados): "
                  + ", ".join(f"{k.split('.')[0]} {m:+.1f}" for k, m in zip(G.MOTORES, todos.mean(0))))
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()


if __name__ == "__main__":
    main()
