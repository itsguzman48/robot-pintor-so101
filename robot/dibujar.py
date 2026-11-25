"""Dibuja trazos (en mm sobre la hoja) interpolando la cuadrícula calibrada.

Alturas: se miden en "levantadas de calibración" (lo que levantaste en el paso 2 de
calibrar_hoja.py, ~1 cm). 0 = tocando el papel como se calibró, 1 = como se calibró
"arriba", 3 = tres veces más alto, etc. Valores intermedios sirven para corregir la presión.
"""
import argparse
import json
import math
import statistics
import time

from robot_utils import activar_par, conectar, leer_articulaciones, suavizar_motores

try:
    import msvcrt  # Windows: leer teclas sin bloquear
except ImportError:
    msvcrt = None


class ParadaEmergencia(Exception):
    pass


def reloj_preciso():
    """En Windows, time.sleep va a saltos de ~16 ms; esto lo baja a ~1 ms."""
    try:
        import ctypes
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass


class Ritmo:
    """Mantiene un ritmo fijo de órdenes por segundo, sin acumular retrasos."""

    def __init__(self, hz):
        self.dt = 1.0 / hz
        self.t = time.perf_counter()

    def esperar(self):
        self.t += self.dt
        while True:
            queda = self.t - time.perf_counter()
            if queda <= 0:
                break
            if queda > 0.002:
                time.sleep(queda - 0.0015)
        if time.perf_counter() - self.t > 0.1:
            self.t = time.perf_counter()


def vaciar_teclado():
    while msvcrt and msvcrt.kbhit():
        msvcrt.getwch()


def comprobar_parada():
    """Si se ha pulsado cualquier tecla, lanza la parada de emergencia."""
    if msvcrt and msvcrt.kbhit():
        vaciar_teclado()
        raise ParadaEmergencia()


class MapaHoja:
    """Convierte (x, y, altura) a ángulos de motor por interpolación bilineal de la cuadrícula."""

    def __init__(self, ruta):
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        self.cols, self.rows, self.paso = d["cols"], d["rows"], d["paso_mm"]
        self.puntos = {(p["fila"], p["col"]): p for p in d["puntos"]}
        faltan = [(f_, c) for f_ in range(self.rows) for c in range(self.cols) if (f_, c) not in self.puntos]
        if faltan:
            raise SystemExit(f"Faltan puntos en la calibración: {faltan}")
        self.claves = list(self.puntos[(0, 0)]["abajo"].keys())
        self.ancho = (self.cols - 1) * self.paso
        self.alto = (self.rows - 1) * self.paso
        # "Subida" igual en toda la hoja: la mediana de lo que se levantó en cada punto.
        # Así un punto donde se levantó poco (o mucho) no estropea esa zona.
        self.subida = {k: statistics.median(p["arriba"][k] - p["abajo"][k] for p in d["puntos"])
                       for k in self.claves}
        self.subida["gripper.pos"] = 0.0 if "gripper.pos" in self.subida else None
        self.subida = {k: v for k, v in self.subida.items() if v is not None}

    def _valor(self, f, c, capa, k):
        f = min(max(f, 0), self.rows - 1)
        c = min(max(c, 0), self.cols - 1)
        return self.puntos[(f, c)][capa][k]

    @staticmethod
    def _cr(p0, p1, p2, p3, t):
        """Catmull-Rom: curva suave que pasa por p1 y p2 sin esquinas entre tramos."""
        return 0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                      + (-p0 + 3 * p1 - 3 * p2 + p3) * t * t * t)

    def _capa(self, x, y, capa):
        x = min(max(x, 0.0), self.ancho)
        y = min(max(y, 0.0), self.alto)
        fx, fy = x / self.paso, y / self.paso
        c0 = min(int(fx), self.cols - 2)
        f0 = min(int(fy), self.rows - 2)
        tx, ty = fx - c0, fy - f0
        salida = {}
        for k in self.claves:
            filas = [self._cr(*(self._valor(f0 + df, c0 + dc, capa, k) for dc in (-1, 0, 1, 2)), tx)
                     for df in (-1, 0, 1, 2)]
            salida[k] = self._cr(*filas, ty)
        return salida

    def articulaciones(self, x, y, altura):
        ab = self._capa(x, y, "abajo")
        return {k: ab[k] + altura * self.subida.get(k, 0.0) for k in self.claves}


def remuestrear(trazo, paso=1.0):
    """Añade puntos intermedios cada `paso` mm para que el movimiento sea continuo."""
    salida = [tuple(trazo[0])]
    for (x0, y0), (x1, y1) in zip(trazo, trazo[1:]):
        n = max(1, int(math.ceil(math.hypot(x1 - x0, y1 - y0) / paso)))
        for i in range(1, n + 1):
            t = i / n
            salida.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return salida


def figuras(ancho, alto):
    cx, cy = ancho / 2, alto / 2
    r = min(ancho, alto) * 0.35
    circulo = [(cx + r * math.cos(2 * math.pi * i / 120), cy + r * math.sin(2 * math.pi * i / 120))
               for i in range(121)]
    estrella = [(cx + r * math.cos(math.pi / 2 + i * 4 * math.pi / 5),
                 cy - r * math.sin(math.pi / 2 + i * 4 * math.pi / 5)) for i in range(6)]
    return {
        "centro": [[(cx, cy)]],
        "esquinas": [[(0, 0)], [(ancho, 0)], [(ancho, alto)], [(0, alto)]],
        "cuadrado": [[(cx - r, cy - r), (cx + r, cy - r), (cx + r, cy + r), (cx - r, cy + r), (cx - r, cy - r)]],
        "circulo": [circulo],
        "estrella": [estrella],
    }


class Plotter:
    def __init__(self, robot, mapa, vel_dibujo, vel_viaje, h_pintar, h_viaje, h_segura, hz=50, acel=30.0):
        self.robot, self.mapa, self.hz, self.acel = robot, mapa, hz, acel
        self.vel_dibujo, self.vel_viaje = vel_dibujo, vel_viaje
        self.h_pintar, self.h_viaje, self.h_segura = h_pintar, h_viaje, h_segura
        self.ultima = None
        self.h_actual = None
        self.ritmo = Ritmo(hz)

    def enviar(self, objetivo):
        self.robot.send_action(objetivo)

    def ir_suave(self, destino, duracion, vigilar=True):
        """Movimiento suave (arranca y frena despacio) desde donde está hasta `destino`."""
        origen = leer_articulaciones(self.robot)
        self.ritmo = Ritmo(self.hz)
        n = max(1, int(duracion * self.hz))
        for i in range(1, n + 1):
            if vigilar:
                comprobar_parada()
            t = i / n
            t = t * t * (3 - 2 * t)
            self.enviar({k: origen[k] + (destino[k] - origen[k]) * t for k in destino})
            self.ritmo.esperar()

    def cambiar_altura(self, x, y, h_desde, h_hasta, duracion):
        """Sube o baja en vertical sobre el punto (x, y), suave."""
        n = max(1, int(duracion * self.hz))
        for i in range(1, n + 1):
            comprobar_parada()
            t = i / n
            t = t * t * (3 - 2 * t)
            self.enviar(self.mapa.articulaciones(x, y, h_desde + (h_hasta - h_desde) * t))
            self.ritmo.esperar()
        self.h_actual = h_hasta

    def recorrer(self, puntos_mm, altura, vel_mm_s):
        """Sigue la línea en un solo movimiento continuo: acelera al empezar y frena al acabar."""
        pts = remuestrear(puntos_mm, 0.2)
        acum = [0.0]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            acum.append(acum[-1] + math.hypot(x1 - x0, y1 - y0))
        total = acum[-1]
        if total < 1e-6:
            self.ultima = pts[-1]
            self.h_actual = altura
            return
        dt = 1.0 / self.hz
        s, v, i = 0.0, 0.0, 0
        self.ritmo = Ritmo(self.hz)
        while True:
            comprobar_parada()
            frenar = math.sqrt(max(2 * self.acel * (total - s), 0.0))
            v = min(vel_mm_s, v + self.acel * dt, frenar)
            v = max(v, 0.5)
            s = min(s + v * dt, total)
            while i < len(acum) - 2 and acum[i + 1] < s:
                i += 1
            tramo = acum[i + 1] - acum[i]
            t = 0.0 if tramo <= 0 else (s - acum[i]) / tramo
            x = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t
            y = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t
            self.enviar(self.mapa.articulaciones(x, y, altura))
            self.ultima = (x, y)
            self.ritmo.esperar()
            if s >= total:
                break
        self.h_actual = altura

    def dibujar(self, trazos, en_el_aire=False):
        h_trazo = self.h_viaje if en_el_aire else self.h_pintar
        for n, trazo in enumerate(trazos, 1):
            x0, y0 = trazo[0]
            print(f"Trazo {n}/{len(trazos)}")
            if self.ultima is None:
                print("  Subiendo a posición segura sobre el primer punto...")
                self.ir_suave(self.mapa.articulaciones(x0, y0, self.h_segura), 5.0)
                self.ultima, self.h_actual = (x0, y0), self.h_segura
                self.cambiar_altura(x0, y0, self.h_segura, self.h_viaje, 2.0)
            else:
                self.recorrer([self.ultima, (x0, y0)], self.h_viaje, self.vel_viaje)
            self.cambiar_altura(x0, y0, self.h_viaje, h_trazo, 1.5)
            self.recorrer(trazo, h_trazo, self.vel_dibujo)
            xf, yf = self.ultima
            self.cambiar_altura(xf, yf, h_trazo, self.h_viaje, 1.0)

    def subir_al_final(self, vigilar=True):
        if self.ultima is None:
            return
        x, y = self.ultima
        if vigilar:
            self.cambiar_altura(x, y, self.h_actual, self.h_segura, 2.0)
        else:
            self.ir_suave(self.mapa.articulaciones(x, y, self.h_segura), 2.5, vigilar=False)

    def congelar(self):
        """Mantiene el brazo exactamente donde está ahora mismo."""
        self.enviar(leer_articulaciones(self.robot))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--id", required=True)
    ap.add_argument("--calib", default="calib_limpia.json")
    ap.add_argument("--figura", choices=["centro", "esquinas", "cuadrado", "circulo", "estrella"], default="cuadrado")
    ap.add_argument("--trazos", help="JSON con lista de trazos [[[x,y],...],...] en mm (sustituye a --figura)")
    ap.add_argument("--vel", type=float, default=12.0, help="mm/s pintando")
    ap.add_argument("--vel-viaje", type=float, default=25.0, help="mm/s con el rotulador levantado")
    ap.add_argument("--altura-pintar", type=float, default=0.0,
                    help="altura al pintar (0 = como se calibró; 0.3 = un poco más arriba si aprieta demasiado)")
    ap.add_argument("--altura-viaje", type=float, default=2.0, help="altura al moverse entre trazos")
    ap.add_argument("--altura-segura", type=float, default=4.0, help="altura al empezar y al terminar")
    ap.add_argument("--acel", type=float, default=30.0, help="mm/s² al arrancar y frenar (menos = más suave)")
    ap.add_argument("--rigidez", type=int, default=16,
                    help="coeficiente P de los motores (LeRobot usa 16). Más alto = más firme pero puede temblar")
    ap.add_argument("--suavidad-motor", type=int, default=20,
                    help="aceleración interna de los motores (254 = brusco, 10-40 = suave)")
    ap.add_argument("--en-el-aire", action="store_true", help="hace todo el recorrido a altura de viaje, sin pintar")
    args = ap.parse_args()

    mapa = MapaHoja(args.calib)
    if args.trazos:
        with open(args.trazos, encoding="utf-8") as f:
            trazos = json.load(f)
    else:
        trazos = figuras(mapa.ancho, mapa.alto)[args.figura]
    print(f"Área calibrada: {mapa.ancho:.0f} x {mapa.alto:.0f} mm")
    print("Subida por unidad de altura (grados):",
          {k.replace(".pos", ""): round(v, 1) for k, v in mapa.subida.items() if abs(v) >= 0.05})
    print("Modo: EN EL AIRE (no pinta)" if args.en_el_aire else "Modo: PINTANDO")
    print(f"Alturas -> pintar {args.altura_pintar} · viaje {args.altura_viaje} · segura {args.altura_segura}"
          f" · velocidad {args.vel} mm/s · rigidez {args.rigidez}")

    reloj_preciso()
    robot = conectar(args.port, args.id, rigidez=args.rigidez)
    activar_par(robot)
    suavizar_motores(robot, args.suavidad_motor)
    plotter = Plotter(robot, mapa, args.vel, args.vel_viaje,
                      args.altura_pintar, args.altura_viaje, args.altura_segura, acel=args.acel)
    if msvcrt:
        print("\n*** PARADA DE EMERGENCIA: pulsa CUALQUIER TECLA (por ejemplo ESPACIO) ***")
    else:
        print("\n*** PARADA DE EMERGENCIA: pulsa Ctrl+C ***")
    for s in (3, 2, 1):
        print(f"Empieza en {s}...")
        time.sleep(1)
    vaciar_teclado()
    try:
        plotter.dibujar(trazos, en_el_aire=args.en_el_aire)
        plotter.subir_al_final()
        print("\nTerminado.")
    except (ParadaEmergencia, KeyboardInterrupt):
        plotter.congelar()
        print("\n!!! PARADA DE EMERGENCIA: el brazo se ha quedado quieto donde estaba.")
        resp = input("Pulsa Enter para subir el rotulador despacio, o escribe n y Enter para dejarlo así: ")
        if resp.strip().lower() != "n":
            plotter.subir_al_final(vigilar=False)
    finally:
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()


if __name__ == "__main__":
    main()
