"""Rellena los puntos que faltan en calib_activa.json (los que el leader no alcanza).

Para cada punto que falta coge el punto más cercano de la MISMA columna que sí
calibraste con el robot sujetándose solo, y le suma la diferencia entre esos dos
puntos en la calibración antigua (calib_limpia.json). Así se aprovecha lo que
medimos con carga y la forma de la calibración antigua para la parte que falta.

Uso:
  python completar_calib.py
Crea calib_activa_completa.json (no toca calib_activa.json).
"""
import argparse
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--activa", default="calib_activa.json")
    ap.add_argument("--antigua", default="calib_limpia.json")
    ap.add_argument("--salida", default="calib_activa_completa.json")
    args = ap.parse_args()

    with open(args.activa, encoding="utf-8") as f:
        act = json.load(f)
    with open(args.antigua, encoding="utf-8") as f:
        ant = json.load(f)

    rows, cols = act["rows"], act["cols"]
    if (ant["rows"], ant["cols"]) != (rows, cols):
        raise SystemExit("Las dos calibraciones no tienen la misma rejilla.")

    pa = {(p["fila"], p["col"]): p for p in act["puntos"]}
    po = {(p["fila"], p["col"]): p for p in ant["puntos"]}

    rellenados = []
    for f in range(rows):
        for c in range(cols):
            if (f, c) in pa:
                continue
            # punto calibrado con carga más cercano en la misma columna
            candidatos = [(abs(f2 - f), f2) for (f2, c2) in pa if c2 == c]
            if not candidatos:
                raise SystemExit(f"La columna {c} no tiene ningún punto calibrado; no puedo rellenar.")
            _, fr = min(candidatos)
            ref_a, ref_o, obj_o = pa[(fr, c)], po[(fr, c)], po[(f, c)]
            nuevo = {"fila": f, "col": c, "relleno": True}
            for capa in ("abajo", "arriba"):
                nuevo[capa] = {k: ref_a[capa][k] + (obj_o[capa][k] - ref_o[capa][k])
                               for k in ref_a[capa]}
            act["puntos"].append(nuevo)
            rellenados.append(f"c{c * 2}-f{f * 2}")

    with open(args.salida, "w", encoding="utf-8") as f:
        json.dump(act, f, indent=1)

    print(f"Puntos calibrados con carga: {len(pa)}")
    print(f"Puntos rellenados: {len(rellenados)} -> {', '.join(rellenados) or 'ninguno'}")
    print(f"Guardado en {args.salida}")


if __name__ == "__main__":
    main()
