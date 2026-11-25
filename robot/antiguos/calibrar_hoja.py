"""Calibración de la hoja: enseñas a mano una cuadrícula de puntos y se guardan los ángulos.

Fila 0 / columna 0 = esquina de origen. Las columnas avanzan en X y las filas en Y.
"""
import argparse
import json
import os

from robot_utils import conectar, desactivar_par, leer_articulaciones


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--id", required=True, help="id de calibración del robot (el que usaron los mentores)")
    ap.add_argument("--cols", type=int, default=9)
    ap.add_argument("--rows", type=int, default=6)
    ap.add_argument("--paso", type=float, default=34.0, help="separación entre puntos en mm")
    ap.add_argument("--salida", default="calib_rapida.json")
    args = ap.parse_args()

    datos = {"cols": args.cols, "rows": args.rows, "paso_mm": args.paso, "puntos": []}
    if os.path.exists(args.salida):
        with open(args.salida, encoding="utf-8") as f:
            previo = json.load(f)
        if (previo["cols"], previo["rows"], previo["paso_mm"]) == (args.cols, args.rows, args.paso):
            datos = previo
            print(f"Continuando calibración existente ({len(datos['puntos'])} puntos ya guardados).")
    hechos = {(p["fila"], p["col"]) for p in datos["puntos"]}

    robot = conectar(args.port, args.id)
    input("\n!! SUJETA EL BRAZO con la mano: voy a quitar la fuerza a los motores. Pulsa Enter...")
    desactivar_par(robot)

    total = args.cols * args.rows
    try:
        for fila in range(args.rows):
            columnas = range(args.cols) if fila % 2 == 0 else reversed(range(args.cols))
            for col in columnas:
                if (fila, col) in hechos:
                    continue
                x, y = col * args.paso, fila * args.paso
                n = len(datos["puntos"]) + 1
                print(f"\n[{n}/{total}] Punto fila={fila} col={col}  ->  x={x:.0f} mm, y={y:.0f} mm"
                      f"  (plantilla: c{round(x / 17)}-f{round(y / 17)})")
                input("   1) Apoya la PUNTA del rotulador justo en el punto (sin aplastar) y pulsa Enter")
                abajo = leer_articulaciones(robot)
                input("   2) Levántalo ~1 cm en vertical, sin girar la muñeca, y pulsa Enter")
                arriba = leer_articulaciones(robot)
                datos["puntos"].append({"fila": fila, "col": col, "x_mm": x, "y_mm": y,
                                        "abajo": abajo, "arriba": arriba})
                with open(args.salida, "w", encoding="utf-8") as f:
                    json.dump(datos, f, indent=2)
                print("   OK guardado:", {k: round(v, 1) for k, v in abajo.items()})
    finally:
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()
    print(f"\nCalibración completa en {args.salida}")


if __name__ == "__main__":
    main()
