"""Muestra un resumen de la calibración de la hoja para detectar puntos raros."""
import json
import sys

ruta = sys.argv[1] if len(sys.argv) > 1 else "calib_rapida.json"
with open(ruta, encoding="utf-8") as f:
    d = json.load(f)
pts = sorted(d["puntos"], key=lambda p: (p["fila"], p["col"]))
claves = [k for k in pts[0]["abajo"] if k != "gripper.pos"]
print(f"{d['cols']}x{d['rows']} puntos, paso {d['paso_mm']} mm\n")
print("Cuánto subiste en cada punto (arriba - abajo), en grados:")
print("punto      " + "  ".join(f"{k.replace('.pos', ''):>13}" for k in claves))
for p in pts:
    dif = [p["arriba"][k] - p["abajo"][k] for k in claves]
    etiqueta = f"c{round(p['x_mm'] / 17)}-f{round(p['y_mm'] / 17)}"
    print(f"{etiqueta:<10} " + "  ".join(f"{v:>13.1f}" for v in dif))
