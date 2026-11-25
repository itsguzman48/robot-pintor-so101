"""Reajusta en el modelo del brazo SOLO dónde está la punta del rotulador respecto a la muñeca.

Úsalo cada vez que cambies cómo va sujeto el rotulador (cinta, pieza 3D...) y hayas calibrado
de nuevo (calib_activa.json). El brazo es el mismo (longitudes, base, motores), así que eso
no se toca: solo se recalcula  e, pa, pb  (dónde está la punta respecto a la muñeca).

Con el modelo corregido, las subidas y bajadas vuelven a ser rectas y sin girar el rotulador.

Uso:
  python reajustar_rotulador.py
  python reajustar_rotulador.py --calib calib_activa.json --modelo modelo_brazo.json
Guarda una copia del modelo anterior en modelo_brazo_anterior.json.
"""
import argparse
import json
import math
import shutil

import numpy as np
from scipy.optimize import least_squares

import geometria as G

LIBRES = ["e", "pa", "pb"]   # o4 fijo: con (pa, pb) ya se puede poner la punta en cualquier sitio


def error_puntos(p, s, q, xy):
    t = G.fk(q, p, s)
    return np.sqrt((t[:, 0] - xy[:, 0]) ** 2 + (t[:, 1] - xy[:, 1]) ** 2 + t[:, 2] ** 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", default="calib_activa.json")
    ap.add_argument("--modelo", default="modelo_brazo.json")
    args = ap.parse_args()

    with open(args.modelo, encoding="utf-8") as f:
        modelo = json.load(f)
    with open(args.calib, encoding="utf-8") as f:
        d = json.load(f)
    pts = d["puntos"]
    q = np.array([[pp["abajo"][k] for k in G.MOTORES] for pp in pts])
    xy = np.array([[pp["x_mm"], pp["y_mm"]] for pp in pts])
    s = np.array(modelo["signos"], float)
    p0 = np.array([modelo["parametros"][n] for n in G.NOMBRES], float)
    idx = [G.NOMBRES.index(n) for n in LIBRES]

    antes = error_puntos(p0, s, q, xy)
    print(f"{len(pts)} puntos. Error con el rotulador antiguo: medio {antes.mean():.1f} mm, máximo {antes.max():.1f} mm")

    def residuos(v):
        p = p0.copy()
        p[idx] = v
        t = G.fk(q, p, s)
        return np.concatenate([t[:, 0] - xy[:, 0], t[:, 1] - xy[:, 1], t[:, 2]])

    # varios puntos de partida, por si el rotulador está muy lejos de donde estaba
    mejor = None
    for dpa in (0, -40, 40, -80, 80):
        for dpb in (0, -50, 50, -100, 100):
            v0 = p0[idx].copy()
            v0[1] += dpa
            v0[2] += dpb
            r = least_squares(residuos, v0, method="lm")
            if mejor is None or r.cost < mejor.cost:
                mejor = r
    p = p0.copy()
    p[idx] = mejor.x
    despues = error_puntos(p, s, q, xy)
    print(f"Error con el rotulador reajustado: medio {despues.mean():.1f} mm, máximo {despues.max():.1f} mm")
    for n, a, b in zip(LIBRES, p0[idx], p[idx]):
        print(f"  {n:3s}: {a:8.2f} -> {b:8.2f} mm")
    if despues.mean() > 5:
        print("[AVISO] El error sigue alto: revisa que el rotulador no se haya movido durante la calibración.")

    # ángulo de la pinza en cada punto (para que siga igual de inclinado en toda la hoja)
    ang = G.angulo_pinza(q, p, s)
    ancho_h, alto_h = (d["cols"] - 1) * d["paso_mm"], (d["rows"] - 1) * d["paso_mm"]
    u, v = xy[:, 0] / ancho_h, xy[:, 1] / alto_h
    if len(pts) >= 9:
        A = np.stack([np.ones_like(u), u, v, u * u, v * v, u * v], -1)
    else:
        A = np.stack([np.ones_like(u), u, v], -1)
    coef = np.linalg.lstsq(A, ang, rcond=None)[0]
    coef = list(map(float, coef)) + [0.0] * (6 - len(coef))

    shutil.copyfile(args.modelo, "modelo_brazo_anterior.json")
    modelo["parametros"] = dict(zip(G.NOMBRES, map(float, p)))
    modelo["angulo_pinza"] = float(np.median(ang))
    modelo["angulo_pinza_coef"] = coef
    modelo["hoja_mm"] = [ancho_h, alto_h]
    modelo.setdefault("informe", {})
    if isinstance(modelo["informe"], dict):
        modelo["informe"]["rotulador_reajustado_con"] = args.calib
    with open(args.modelo, "w", encoding="utf-8") as f:
        json.dump(modelo, f, indent=2)

    # comprobación: ¿llega a subir y bajar en toda la hoja?
    M = G.Modelo(args.modelo)
    fuera = []
    for x in np.linspace(0, ancho_h, 5):
        for y in np.linspace(0, alto_h, 3):
            for z in (0.0, 15.0, 40.0):
                try:
                    M._ik(x, y, z, M.theta_en(x, y))
                except ValueError:
                    fuera.append((round(x), round(y), z))
    if fuera:
        print(f"[AVISO] Con este rotulador no alcanza: {fuera[:6]}{' ...' if len(fuera) > 6 else ''}")
    print(f"\nGuardado {args.modelo} (el anterior está en modelo_brazo_anterior.json).")


if __name__ == "__main__":
    main()
