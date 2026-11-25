"""Deja el rotulador con la MISMA inclinación en toda la hoja: la de un punto que tú elijas
(el que está perpendicular al papel).

Para cada punto calibrado recalcula la postura de los motores para que la punta siga en el mismo
sitio pero con la muñeca girada hasta la inclinación de referencia. Se hace como corrección
sobre lo que tú mediste (no sustituye tus puntos), así no se pierde lo que cede el brazo por el peso.
Hace lo mismo con las posturas "encima" que le enseñaste para levantar, si las hay.

También deja el modelo con esa inclinación fija, para que al viajar por el aire no la cambie.

Uso:
  python fijar_perpendicular.py --referencia c8-f4
Copias de seguridad: calib_activa_antes_perpendicular.json y modelo_brazo_antes_perpendicular.json
"""
import argparse
import json
import math
import re
import shutil

import numpy as np

import geometria as G


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--referencia", default="c8-f4", help="punto donde el rotulador está perpendicular, p.ej. c8-f4")
    ap.add_argument("--calib", default="calib_activa.json")
    ap.add_argument("--modelo", default="modelo_brazo.json")
    args = ap.parse_args()

    m = re.fullmatch(r"c(\d+)-f(\d+)", args.referencia.strip().lower())
    if not m:
        raise SystemExit("La referencia tiene que ser tipo c8-f4")
    x_ref, y_ref = int(m.group(1)) * 17.0, int(m.group(2)) * 17.0

    with open(args.calib, encoding="utf-8") as f:
        d = json.load(f)
    M = G.Modelo(args.modelo)
    pts = d["puntos"]
    ref = [p for p in pts if abs(p["x_mm"] - x_ref) < 1 and abs(p["y_mm"] - y_ref) < 1]
    if not ref:
        raise SystemExit(f"No hay ningún punto calibrado en {args.referencia}.")
    ref = ref[0]

    def angulo(postura):
        return float(G.angulo_pinza(np.array([[postura[k] for k in G.MOTORES]]), M.p, M.s)[0])

    th_ref = angulo(ref["abajo"])
    h = float(d.get("altura_arriba_mm", 10.0))
    print(f"Inclinación de referencia ({args.referencia}): {math.degrees(th_ref):.1f}°\n")
    print(f"{'punto':8s} {'tocando':>9s} {'encima':>9s}   (grados que se corrige la muñeca)")

    def corregir(postura, x, y, z):
        th = angulo(postura)
        try:
            q_viejo = M._ik(x, y, z, th)
            q_nuevo = M._ik(x, y, z, th_ref)
        except ValueError:
            return postura, None
        nueva = dict(postura)
        for k in G.MOTORES:
            nueva[k] = postura[k] + q_nuevo[k] - q_viejo[k]
        return nueva, math.degrees(th_ref - th)

    no_llega = []
    for p in pts:
        x, y = p["x_mm"], p["y_mm"]
        p["abajo"], da = corregir(p["abajo"], x, y, 0.0)
        txt_a = f"{da:+8.1f}°" if da is not None else "   no llega"
        txt_b = ""
        if p.get("arriba_medido"):
            p["arriba"], db = corregir(p["arriba"], x, y, h)
            txt_b = f"{db:+8.1f}°" if db is not None else "   no llega"
        else:
            # sin enseñar: "encima" = tocando + subir en vertical con la inclinación fija
            try:
                q0, q1 = M._ik(x, y, 0.0, th_ref), M._ik(x, y, 12.0, th_ref)
                p["arriba"] = {k: (p["abajo"][k] + q1[k] - q0[k]) if k in G.MOTORES else v
                               for k, v in p["abajo"].items()}
            except ValueError:
                pass    # en esta esquina el modelo no llega con esa inclinación: se deja como estaba
        if da is None:
            no_llega.append(f"c{round(x / 17)}-f{round(y / 17)}")
        print(f"c{round(x / 17)}-f{round(y / 17):<4d} {txt_a} {txt_b}")

    shutil.copyfile(args.calib, args.calib.replace(".json", "_antes_perpendicular.json"))
    shutil.copyfile(args.modelo, args.modelo.replace(".json", "_antes_perpendicular.json"))
    d["perpendicular_ref"] = args.referencia
    with open(args.calib, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    with open(args.modelo, encoding="utf-8") as f:
        mod = json.load(f)
    mod["angulo_pinza"] = th_ref
    mod["angulo_pinza_coef"] = [th_ref, 0, 0, 0, 0, 0]
    with open(args.modelo, "w", encoding="utf-8") as f:
        json.dump(mod, f, indent=2)
    if no_llega:
        print(f"\n[AVISO] Con esa inclinación no llega a: {', '.join(no_llega)} (esos puntos se quedan como estaban)")
    print(f"\nGuardado {args.calib} y {args.modelo}.")


if __name__ == "__main__":
    main()
