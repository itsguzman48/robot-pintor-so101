"""SVG → trazos en milímetros de la hoja, listos para el robot.

Pasos:
  1. leer   : todas las formas del SVG (path, circle, ellipse, rect, line, polyline, polygon)
              se convierten en polilíneas, muestreando las curvas cada poco.
  2. orientar: girar/espejar el dibujo para que salga derecho visto desde donde mira la gente.
  3. encajar: escalar manteniendo proporciones y centrar en la hoja, con margen.
  4. limpiar: quitar puntos repetidos, simplificar (Douglas-Peucker) y descartar trazos minúsculos.
  5. ordenar: elegir el orden y el sentido de cada trazo para viajar lo mínimo en el aire.

Coordenadas de la hoja (las mismas que la calibración):
  x de 0 a ancho (columna c0 -> c16), y de 0 a alto (fila f0 -> f10), en mm.

Uso suelto (para probar sin robot):
  python svg_a_trazos.py dibujo.svg
  python svg_a_trazos.py dibujo.svg --png vista.png --json trazos.json
"""
import argparse
import io
import json
import math
from dataclasses import dataclass, field

import numpy as np
from svgelements import SVG, Close, Line, Move, Path, Shape, SVGText


@dataclass
class Trazo:
    puntos: np.ndarray            # (N, 2) en mm
    color: str = "#000000"        # para cuando haya varios rotuladores
    cerrado: bool = False
    punto: bool = False           # solo tocar el papel (ojos diminutos)

    @property
    def longitud(self):
        return float(np.linalg.norm(np.diff(self.puntos, axis=0), axis=1).sum()) if len(self.puntos) > 1 else 0.0


@dataclass
class Opciones:
    ancho: float = 272.0          # mm de la zona calibrada
    alto: float = 170.0
    tamano: float = 150.0         # lado máximo del dibujo en mm
    margen: float = 8.0           # mm libres hasta el borde de la zona calibrada
    girar: int = 0                # 0, 90, 180 o 270 grados
    espejo: bool = False          # reflejar izquierda-derecha
    paso_curva: float = 0.8       # mm entre puntos al muestrear curvas
    tolerancia: float = 0.15      # mm de simplificación (Douglas-Peucker)
    minimo: float = 0.8           # mm: trazos más cortos se descartan
    min_detalle: float = 8.0      # mm: las formas cerradas más pequeñas (ojos...) se agrandan a este tamaño
    punto_max: float = 6.5        # mm: las que son aún más pequeñas (un "punto" del SVG) se pintan como un punto
    inicio: tuple = field(default=None)  # (x, y) desde donde empieza el brazo; None = centro


# ---------- 1. leer ----------

def _color(elemento):
    try:
        c = elemento.stroke
        if c is not None and c.value is not None:
            return c.hex[:7]
    except Exception:
        pass
    return "#000000"


def _muestrear_segmento(seg, paso):
    """Puntos de un segmento (sin el inicial). Las rectas solo necesitan el final."""
    if isinstance(seg, (Line, Close)):
        return [seg.end]
    try:
        largo = seg.length(error=1e-4)
    except Exception:
        largo = abs(seg.end - seg.start) if seg.start is not None else 0
    n = max(2, int(math.ceil(largo / paso)))
    return [seg.point(t) for t in np.linspace(0, 1, n + 1)[1:]]


def leer_svg(texto, paso):
    """Devuelve trazos en las unidades del propio SVG (y hacia abajo)."""
    svg = SVG.parse(io.StringIO(texto), reify=True)
    trazos = []
    for el in svg.elements():
        if not isinstance(el, Shape) or isinstance(el, SVGText):
            continue
        try:
            camino = Path(el)
            camino.reify()
        except Exception:
            continue
        color = _color(el)
        actual = []
        for seg in camino:
            if isinstance(seg, Move):
                if len(actual) > 1:
                    trazos.append(Trazo(np.array(actual, float), color))
                actual = [(seg.end.x, seg.end.y)]
                continue
            if not actual and seg.start is not None:
                actual = [(seg.start.x, seg.start.y)]
            for p in _muestrear_segmento(seg, paso):
                actual.append((p.x, p.y))
        if len(actual) > 1:
            trazos.append(Trazo(np.array(actual, float), color))
    return trazos


# ---------- 2. orientar y 3. encajar ----------

def orientar(trazos, girar, espejo):
    if girar % 360 not in (0, 90, 180, 270):
        raise ValueError("girar tiene que ser 0, 90, 180 o 270")
    for t in trazos:
        x, y = t.puntos[:, 0].copy(), t.puntos[:, 1].copy()
        if espejo:
            x = -x
        g = girar % 360
        if g == 90:
            x, y = -y, x
        elif g == 180:
            x, y = -x, -y
        elif g == 270:
            x, y = y, -x
        t.puntos = np.stack([x, y], 1)
    return trazos


def encajar(trazos, op):
    """Escala (sin deformar) y centra el dibujo en la hoja."""
    todo = np.vstack([t.puntos for t in trazos])
    mn, mx = todo.min(0), todo.max(0)
    tam = np.maximum(mx - mn, 1e-9)
    hueco = np.array([min(op.tamano, op.ancho - 2 * op.margen), min(op.tamano, op.alto - 2 * op.margen)])
    escala = float(min(hueco / tam))
    centro_svg = (mn + mx) / 2
    centro_hoja = np.array([op.ancho / 2, op.alto / 2])
    for t in trazos:
        t.puntos = (t.puntos - centro_svg) * escala + centro_hoja
    return trazos, escala


# ---------- 4. limpiar ----------

def _douglas_peucker(p, tol):
    if len(p) < 3:
        return p
    a, b = p[0], p[-1]
    ab = b - a
    n = np.linalg.norm(ab)
    if n < 1e-12:
        d = np.linalg.norm(p - a, axis=1)
    else:
        d = np.abs(ab[0] * (p[:, 1] - a[1]) - ab[1] * (p[:, 0] - a[0])) / n
    i = int(np.argmax(d))
    if d[i] > tol:
        izq = _douglas_peucker(p[: i + 1], tol)
        der = _douglas_peucker(p[i:], tol)
        return np.vstack([izq[:-1], der])
    return np.vstack([a, b])


def agrandar_detalles(trazos, minimo, punto_max=0.0):
    """Formas cerradas diminutas: si son un punto (más pequeñas que punto_max) se pintan como un
    punto (bajar y subir); si son algo más grandes (ojos...) se agrandan hasta `minimo`, porque con
    un rotulador gordo y la holgura del brazo un círculo de 3 mm sale como una mancha."""
    for t in trazos:
        p = t.puntos
        if len(p) < 3 or np.linalg.norm(p[0] - p[-1]) > 0.3:
            continue
        tam = float((p.max(0) - p.min(0)).max())
        c = (p.max(0) + p.min(0)) / 2
        if tam < punto_max:
            t.puntos, t.punto = c[None, :], True
        elif 1e-6 < tam < minimo:
            t.puntos = c + (p - c) * (minimo / tam)
    return trazos


def limpiar(trazos, op):
    salida = []
    for t in trazos:
        if t.punto:
            salida.append(t)
            continue
        p = t.puntos
        # quitar puntos repetidos seguidos
        keep = np.concatenate([[True], np.linalg.norm(np.diff(p, axis=0), axis=1) > 1e-3])
        p = p[keep]
        if len(p) < 2:
            continue
        cerrado = np.linalg.norm(p[0] - p[-1]) < 0.3
        p = _douglas_peucker(p, op.tolerancia)
        t = Trazo(p, t.color, cerrado)
        if t.longitud >= op.minimo:
            salida.append(t)
    return salida


# ---------- 5. ordenar ----------

def ordenar(trazos, inicio):
    """Vecino más cercano: siempre el trazo cuyo extremo está más cerca del rotulador.
    Los trazos abiertos se pueden hacer al revés; los cerrados empiezan en el punto más cercano.
    Dentro de cada color se ordena por separado (los colores van seguidos para cambiar pocas veces)."""
    pendientes = list(trazos)
    pos = np.array(inicio, float)
    orden = []
    while pendientes:
        mejor, mejor_d, modo = None, float("inf"), None
        for i, t in enumerate(pendientes):
            if orden and t.color != orden[-1].color and any(p.color == orden[-1].color for p in pendientes):
                continue
            if t.cerrado:
                d = np.linalg.norm(t.puntos[:-1] - pos, axis=1)
                k = int(np.argmin(d))
                if d[k] < mejor_d:
                    mejor, mejor_d, modo = i, d[k], ("rotar", k)
            else:
                d0 = np.linalg.norm(t.puntos[0] - pos)
                d1 = np.linalg.norm(t.puntos[-1] - pos)
                if d0 < mejor_d:
                    mejor, mejor_d, modo = i, d0, ("normal", 0)
                if d1 < mejor_d:
                    mejor, mejor_d, modo = i, d1, ("reves", 0)
        t = pendientes.pop(mejor)
        if modo[0] == "reves":
            t.puntos = t.puntos[::-1].copy()
        elif modo[0] == "rotar" and modo[1]:
            k = modo[1]
            anillo = t.puntos[:-1]
            anillo = np.vstack([anillo[k:], anillo[:k]])
            t.puntos = np.vstack([anillo, anillo[:1]])
        orden.append(t)
        pos = t.puntos[-1]
    return orden


def recorrido_en_aire(trazos, inicio):
    pos = np.array(inicio, float)
    total = 0.0
    for t in trazos:
        total += float(np.linalg.norm(t.puntos[0] - pos))
        pos = t.puntos[-1]
    return total


# ---------- todo junto ----------

def svg_a_trazos(texto, op=None):
    """SVG (texto) → (lista de Trazo en mm de la hoja, informe dict)."""
    op = op or Opciones()
    crudos = leer_svg(texto, paso=1.0)
    if not crudos:
        raise ValueError("El SVG no tiene ninguna forma que se pueda dibujar.")
    # muestreo fino en unidades del SVG: re-muestrear tras conocer la escala
    _, escala = encajar(orientar([Trazo(t.puntos.copy()) for t in crudos], op.girar, op.espejo), op)
    crudos = leer_svg(texto, paso=max(op.paso_curva / escala, 1e-3))
    trazos = orientar(crudos, op.girar, op.espejo)
    trazos, escala = encajar(trazos, op)
    trazos = agrandar_detalles(trazos, op.min_detalle, op.punto_max)
    trazos = limpiar(trazos, op)
    if not trazos:
        raise ValueError("Después de limpiar no queda ningún trazo (¿dibujo demasiado pequeño?).")
    inicio = op.inicio or (op.ancho / 2, op.alto / 2)
    sin_orden = recorrido_en_aire(trazos, inicio)
    trazos = ordenar(trazos, inicio)
    todo = np.vstack([t.puntos for t in trazos])
    informe = {
        "trazos": len(trazos),
        "puntos": int(sum(len(t.puntos) for t in trazos)),
        "mm_pintando": round(sum(t.longitud for t in trazos), 1),
        "mm_en_el_aire": round(recorrido_en_aire(trazos, inicio), 1),
        "mm_en_el_aire_sin_ordenar": round(sin_orden, 1),
        "escala_mm_por_unidad": round(escala, 4),
        "caja_mm": [round(float(v), 1) for v in (*todo.min(0), *todo.max(0))],
        "colores": sorted({t.color for t in trazos}),
    }
    return trazos, informe


def a_lista(trazos):
    """Formato que entiende dibujar_ik.py --trazos: [[[x, y], ...], ...]"""
    return [[[round(float(x), 3), round(float(y), 3)] for x, y in t.puntos] for t in trazos]


def guardar_png(trazos, ruta, op=None, titulo=""):
    """Vista previa de lo que hará el robot: trazos en negro, viajes en el aire en rojo discontinuo."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    op = op or Opciones()
    fig, ax = plt.subplots(figsize=(8, 8 * op.alto / op.ancho))
    ax.add_patch(plt.Rectangle((0, 0), op.ancho, op.alto, fill=False, ec="#999", lw=1))
    pos = np.array(op.inicio or (op.ancho / 2, op.alto / 2))
    for i, t in enumerate(trazos):
        ax.plot([pos[0], t.puntos[0, 0]], [pos[1], t.puntos[0, 1]], "--", color="#d33", lw=0.6)
        ax.plot(t.puntos[:, 0], t.puntos[:, 1], "-", color=t.color if t.color != "#000000" else "k", lw=1.4)
        ax.plot(*t.puntos[0], "o", color="#2a7", ms=3)
        pos = t.puntos[-1]
    ax.set_xlim(-5, op.ancho + 5)
    ax.set_ylim(op.alto + 5, -5)  # y hacia abajo, como en la plantilla
    ax.set_aspect("equal")
    ax.set_title(titulo or "negro: pinta · rojo: viaja en el aire · verde: empieza el trazo", fontsize=9)
    fig.tight_layout()
    fig.savefig(ruta, dpi=120)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("svg")
    ap.add_argument("--json", help="guardar trazos para dibujar_ik.py --trazos")
    ap.add_argument("--png", help="guardar vista previa")
    ap.add_argument("--tamano", type=float, default=150.0)
    ap.add_argument("--girar", type=int, default=0)
    ap.add_argument("--espejo", action="store_true")
    args = ap.parse_args()
    with open(args.svg, encoding="utf-8") as f:
        texto = f.read()
    op = Opciones(tamano=args.tamano, girar=args.girar, espejo=args.espejo)
    trazos, informe = svg_a_trazos(texto, op)
    print(json.dumps(informe, ensure_ascii=False, indent=1))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(a_lista(trazos), f)
        print(f"Trazos guardados en {args.json}")
    if args.png:
        guardar_png(trazos, args.png, op)
        print(f"Vista previa en {args.png}")


if __name__ == "__main__":
    main()
