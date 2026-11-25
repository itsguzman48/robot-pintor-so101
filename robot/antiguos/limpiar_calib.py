"""Limpia la calibración de la hoja.

1. Compara cada punto "abajo" con lo que dicen sus vecinos (una superficie suave local).
2. Los puntos que se salen mucho (mal tocados) se sustituyen por lo que dicen sus vecinos.
3. El resto se suaviza un poco para quitar el temblor de haberlo hecho a mano.
4. "Arriba" pasa a ser la misma subida en toda la hoja (la mediana de lo que subiste).

Uso:  python limpiar_calib.py calib_rapida.json calib_limpia.json
"""
import json
import statistics
import sys

import numpy as np

SUAVIZADO = 0.3    # 0 = deja tus puntos tal cual, 1 = solo lo que dicen sus vecinos
UMBRAL_MIN = 1.5   # grados: por debajo de esto nunca se considera raro
RADIO = 2          # vecinos a cada lado que se usan para juzgar un punto


def prediccion_vecinos(grid, f, c):
    """Lo que 'debería' valer el punto (f, c) según una superficie suave ajustada a sus vecinos."""
    filas, cols = grid.shape
    X, Y = [], []
    for df in range(-RADIO, RADIO + 1):
        for dc in range(-RADIO, RADIO + 1):
            ff, cc = f + df, c + dc
            if (df, dc) == (0, 0) or not (0 <= ff < filas and 0 <= cc < cols):
                continue
            X.append([1, dc, df, dc * dc, df * df, dc * df])
            Y.append(grid[ff, cc])
    coef = np.linalg.lstsq(np.array(X, float), np.array(Y, float), rcond=None)[0]
    return float(coef[0])


def predicciones(grid):
    return np.array([[prediccion_vecinos(grid, f, c) for c in range(grid.shape[1])]
                     for f in range(grid.shape[0])])


def main():
    entrada = sys.argv[1] if len(sys.argv) > 1 else "calib_rapida.json"
    salida = sys.argv[2] if len(sys.argv) > 2 else "calib_limpia.json"
    with open(entrada, encoding="utf-8") as f:
        d = json.load(f)
    pts = d["puntos"]
    claves = list(pts[0]["abajo"].keys())
    ancho = (d["cols"] - 1) * d["paso_mm"]
    alto = (d["rows"] - 1) * d["paso_mm"]
    idx = {(p["fila"], p["col"]): i for i, p in enumerate(pts)}

    subida = {k: statistics.median(p["arriba"][k] - p["abajo"][k] for p in pts) for k in claves}
    if "gripper.pos" in subida:
        subida["gripper.pos"] = 0.0

    corregidos = {}
    dudosos = {}
    filas, cols = d["rows"], d["cols"]
    interior = np.zeros((filas, cols), bool)
    interior[1:-1, 1:-1] = True  # en los bordes la comparación con vecinos no es fiable
    nuevo = {k: [0.0] * len(pts) for k in claves}
    for k in claves:
        grid = np.array([[pts[idx[(f, c)]]["abajo"][k] for c in range(d["cols"])] for f in range(d["rows"])])
        if k == "gripper.pos":
            for (f, c), i in idx.items():
                nuevo[k][i] = float(grid[f, c])
            continue
        for _ in range(2):  # busca raros, los corrige y vuelve a mirar
            pred = predicciones(grid)
            res = grid - pred
            mad = np.median(np.abs(res - np.median(res))) * 1.4826
            umbral = max(3 * mad, UMBRAL_MIN)
            raros = np.abs(res) > umbral
            for f, c in zip(*np.nonzero(raros & interior)):
                corregidos.setdefault(idx[(f, c)], []).append(f"{k.replace('.pos', '')} {res[f, c]:+.1f}°")
                grid[f, c] = pred[f, c]
            for f, c in zip(*np.nonzero(raros & ~interior)):
                dudosos.setdefault(idx[(f, c)], set()).add(k.replace('.pos', ''))
        pred = predicciones(grid)
        final = np.where(interior, (1 - SUAVIZADO) * grid + SUAVIZADO * pred, grid)
        for (f, c), i in idx.items():
            nuevo[k][i] = float(final[f, c])

    for i, p in enumerate(pts):
        p["abajo"] = {k: nuevo[k][i] for k in claves}
        p["arriba"] = {k: p["abajo"][k] + subida[k] for k in claves}
    d["limpiado"] = {"desde": entrada, "suavizado": SUAVIZADO, "corregidos": len(corregidos)}
    with open(salida, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)

    print(f"Subida común (grados): { {k.replace('.pos', ''): round(s, 1) for k, s in subida.items() if abs(s) >= 0.05} }")
    if corregidos:
        print(f"\nPuntos corregidos por estar fuera de lo normal ({len(corregidos)}):")
        for i, motivos in sorted(corregidos.items(), key=lambda t: (pts[t[0]]["fila"], pts[t[0]]["col"])):
            p = pts[i]
            print(f"  c{round(p['x_mm'] / 17)}-f{round(p['y_mm'] / 17)}: " + ", ".join(motivos))
    else:
        print("\nNingún punto raro en el interior: solo se ha suavizado un poco.")
    if dudosos:
        print("\nPuntos del borde que parecen raros (no los toco; si dibuja mal ahí, recalíbralos):")
        for i in sorted(dudosos, key=lambda i: (pts[i]["fila"], pts[i]["col"])):
            p = pts[i]
            print(f"  c{round(p['x_mm'] / 17)}-f{round(p['y_mm'] / 17)}: " + ", ".join(sorted(dudosos[i])))
    print(f"\nGuardado en {salida}")


if __name__ == "__main__":
    main()
