"""Calcula la geometría real del brazo a partir de tu calibración (y del barrido, si lo hay).

Uso:
  python ajustar_modelo.py                       (usa calib_rapida.json)
  python ajustar_modelo.py calib_rapida.json barrido.json   (añade el barrido, si es coherente)
Crea modelo_brazo.json, que es lo que usa dibujar_ik.py.
"""
import json
import math
import os
import sys

import numpy as np

import geometria as G


def main():
    ruta_calib = sys.argv[1] if len(sys.argv) > 1 else "calib_rapida.json"
    ruta_barrido = sys.argv[2] if len(sys.argv) > 2 else None
    with open(ruta_calib, encoding="utf-8") as f:
        d = json.load(f)
    pts = d["puntos"]
    q_pts = np.array([[p["abajo"][k] for k in G.MOTORES] for p in pts])
    q_arriba = np.array([[p["arriba"][k] for k in G.MOTORES] for p in pts])
    xy = np.array([[p["x_mm"], p["y_mm"]] for p in pts])
    q_plano = None
    if ruta_barrido:
        with open(ruta_barrido, encoding="utf-8") as f:
            b = json.load(f)
        q_plano = np.array([[m[k] for k in G.MOTORES] for m in b["muestras"]])
        giro_b = np.median([m["wrist_roll.pos"] for m in b["muestras"]])
        giro_c = np.median([p["abajo"]["wrist_roll.pos"] for p in pts])
        if abs(giro_b - giro_c) > 3:
            print(f"[aviso] En el barrido la muñeca estaba girada {giro_b - giro_c:+.0f}° respecto a la calibración:"
                  " el rotulador estaba en otra posición, así que no uso el barrido.")
            q_plano = None
        else:
            print(f"Usando también el barrido: {len(q_plano)} posturas tocando el papel")

    print("Calculando la geometría del brazo (tarda unos segundos)...")
    p, s, inf = G.ajustar(q_pts, xy, q_plano, q_arriba=q_arriba)

    # quitar puntos que no encajan (mal tocados) y repetir
    t = G.fk(q_pts, p, s)
    err = np.sqrt((t[:, 0] - xy[:, 0]) ** 2 + (t[:, 1] - xy[:, 1]) ** 2 + t[:, 2] ** 2)
    umbral = max(4.0, 3 * np.median(err))
    malos = err > umbral
    if malos.any():
        print("Puntos que no encajan con el resto (se ignoran):")
        for i in np.nonzero(malos)[0]:
            print(f"  c{round(xy[i, 0] / 17)}-f{round(xy[i, 1] / 17)}: {err[i]:.1f} mm")
        buenos = ~malos
        p, s, inf = G.ajustar(q_pts[buenos], xy[buenos], q_plano, q_arriba=q_arriba[buenos])

    ang = G.angulo_pinza(q_pts, p, s)
    theta = float(np.median(ang))
    ancho_h, alto_h = (d["cols"] - 1) * d["paso_mm"], (d["rows"] - 1) * d["paso_mm"]
    u, v = xy[:, 0] / ancho_h, xy[:, 1] / alto_h
    A = np.stack([np.ones_like(u), u, v, u * u, v * v, u * v], -1)
    theta_coef = np.linalg.lstsq(A, ang, rcond=None)[0]
    rel = np.array([(s[2] * math.radians(q[2]) + p[11] + math.pi) % (2 * math.pi) - math.pi for q in q_pts])
    codo = 1 if np.median(rel) >= 0 else -1
    todos = [pp["abajo"] for pp in pts] + [pp["arriba"] for pp in pts]
    fijos = {k: float(np.median([m[k] for m in todos])) for k in pts[0]["abajo"] if k not in G.MOTORES}
    limites = {k: [float(min(m[k] for m in todos)), float(max(m[k] for m in todos))] for k in G.MOTORES}
    modelo = {"parametros": dict(zip(G.NOMBRES, map(float, p))), "signos": [int(v) for v in s],
              "angulo_pinza": theta, "angulo_pinza_coef": [float(c) for c in theta_coef], "codo": codo, "fijos": fijos, "limites": limites,
              "hoja_mm": [(d["cols"] - 1) * d["paso_mm"], (d["rows"] - 1) * d["paso_mm"]], "informe": inf}
    with open("modelo_brazo.json", "w", encoding="utf-8") as f:
        json.dump(modelo, f, indent=2)

    # comprobación: el modelo vuelve a calcular tus puntos y llega a toda la hoja
    M = G.Modelo("modelo_brazo.json")
    difs = []
    ancho, alto = modelo["hoja_mm"]
    fuera = []
    for x in np.linspace(0, ancho, 9):
        for y in np.linspace(0, alto, 6):
            for z in (0.0, 15.0, 40.0):
                try:
                    qi = M.ik(x, y, z)
                    t = G.fk(np.array([[qi[k] for k in G.MOTORES]]), p, s)[0]
                    difs.append(float(np.abs(t - [x, y, z]).max()))
                except ValueError:
                    fuera.append((round(x), round(y), round(z)))

    print("\n=== RESULTADO ===")
    print(f"Brazo: L1 = {p[5]:.0f} mm, L2 = {p[6]:.0f} mm, hombro a {p[3]:.0f} mm sobre el papel")
    print(f"Base en x = {p[0]:.0f} mm, y = {p[1]:.0f} mm (respecto a la esquina c0-f0)")
    print(f"Error en tus puntos: medio {inf['err_xy_medio']:.1f} mm, máximo {inf['err_xy_max']:.1f} mm (en la hoja)")
    print(f"Altura en tus puntos: media {inf['err_z_medio']:.1f} mm, máxima {inf['err_z_max']:.1f} mm (debería ser 0)")
    if "barrido_z_medio" in inf:
        print(f"Altura en el barrido: media {inf['barrido_z_medio']:.1f} mm, máxima {inf['barrido_z_max']:.1f} mm")
    print(f"'Arriba' de la calibración: unos {inf.get('arriba_z_mediana', float('nan')):.0f} mm sobre el papel")
    print(f"Inclinación de la pinza: de {math.degrees(ang.min()):.0f}° a {math.degrees(ang.max()):.0f}° según la zona")
    if difs:
        print(f"Comprobación del cálculo inverso: error máximo {max(difs):.3f} mm")
    if fuera:
        print(f"[aviso] {len(fuera)} de {9 * 6 * 3} posiciones de prueba quedan fuera del alcance, p. ej. {fuera[:4]}")
    ok = 85 < p[5] + 0 < 140 and 100 < p[6] < 170 and 5 < inf.get("arriba_z_mediana", 0) < 25
    print("\nModelo " + ("COHERENTE: se puede usar para subir y bajar el rotulador" if ok else
                         "RARO: no lo uses, pásame el resultado"))
    print("Guardado en modelo_brazo.json")


if __name__ == "__main__":
    main()
