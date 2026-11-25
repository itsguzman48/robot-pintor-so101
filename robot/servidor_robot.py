"""Servidor del robot pintor: la web manda un SVG y el brazo lo dibuja.

    web (robot-dibujante.html)
        │  POST /draw  {"svg": "<svg ...>"}      GET /status (cada 500 ms)      POST /stop
        ▼
    servidor_robot.py
        1. svg_a_trazos   SVG → trazos en mm de la hoja (encajado, limpio y ordenado)
        2. Planificador   trazos → recorrido 3D (bajar, pintar, subir, viajar) → ángulos de motor
                          + comprobación previa: si algo no se alcanza, NO se mueve nada
        3. Brazo          manda los ángulos al SO-101 a ritmo fijo (o lo simula)

API (CORS abierto: funciona con la web abierta con doble clic):
    POST /draw    {"svg": "...", "titulo": "...", "opciones": {...}}  (titulo y opciones opcionales)
                  202 {"ok": true, "trazos": 12, "longitud_mm": 840, "duracion_s": 41, ...}
                  400 SVG inválido · 422 fuera de alcance · 409 ocupado · 503 robot no conectado
    GET  /status  {"estado": "libre|planificando|dibujando|terminado|parado|error",
                   "trazo_actual": 5, "trazos_total": 12, "progreso": 0.4, "error": null, ...}
                  Tras terminado/parado/error vuelve a "libre" en la siguiente consulta.
    POST /stop    levanta el rotulador, para y vuelve a reposo. Siempre 200.
    POST /preview igual que /draw pero sin mover nada (devuelve también los trazos en mm).
    (/estado y /parar siguen funcionando como alias de /status y /stop.)

Parada de emergencia también con CUALQUIER TECLA en esta ventana.
Cada dibujo se guarda en trabajos/ (SVG + trazos + resultado): sirve de dataset.

Uso:
    python servidor_robot.py --simular                          (sin robot, para probar la web)
    python servidor_robot.py --port %PORT% --id %ROBOT_ID%      (con el robot)
"""
import argparse
import datetime as dt
import json
import os
import re
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

import geometria as G
from dibujar_ik import (MapaHibrido, Ritmo, anticipar, cargar_retraso, compensar_holgura, construir_recorrido, insertar_pausas, ir_suave,
                        pausas_arriba,
                        muestrear_en_tiempo, perfil_velocidad, redondear_y_muestrear, reloj_preciso)
from svg_a_trazos import Opciones, a_lista, svg_a_trazos

try:
    import msvcrt
except ImportError:
    msvcrt = None

FINALES = ("terminado", "parado", "error")
RGB = {"azul": (30, 70, 200), "rojo": (210, 40, 40), "verde": (40, 150, 60), "negro": (20, 20, 20),
       "naranja": (240, 140, 30), "morado": (120, 50, 160), "amarillo": (230, 200, 30)}


def elegir_pluma(color_hex, plumas):
    """Color del trazo en el SVG -> rotulador. Negro/gris -> el primero."""
    try:
        r, g, b = (int(color_hex[i:i + 2], 16) for i in (1, 3, 5))
    except Exception:
        return plumas[0]
    if max(r, g, b) - min(r, g, b) < 40:          # negro, gris o blanco: sin color
        return plumas[0]
    def dist(p):
        pr, pg, pb = RGB.get(p, (20, 20, 20))
        return (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
    return min(plumas, key=dist)
OCUPADO = ("planificando", "dibujando", "parando")


class FueraDeAlcance(ValueError):
    pass


class Ocupado(RuntimeError):
    pass


class SinRobot(RuntimeError):
    pass


# ======================================================================
# Planificación: trazos en mm → lista de órdenes (ángulos) a ritmo fijo
# ======================================================================

class MapaSimulado:
    """Solo para --simular sin archivos de calibración: 'ángulos' inventados a partir de x, y, z."""

    def __init__(self, ancho=272.0, alto=136.0):
        self.ancho, self.alto = ancho, alto

    def articulaciones(self, x, y, z, corregir=True):
        return {G.MOTORES[0]: x / 10, G.MOTORES[1]: y / 10, G.MOTORES[2]: z / 10, G.MOTORES[3]: 0.0}


class Planificador:
    def __init__(self, mapas, a):
        self.mapas, self.a = mapas, a            # {nombre_rotulador: mapa}
        self.retraso = cargar_retraso(a.retraso)
        self.desplazar = {}                      # {rotulador: (dx, dy) mm}

    def planificar_colores(self, grupos, en_el_aire=False):
        """grupos: [(rotulador, trazos_mm), ...] en orden. Entre grupos: sube a la altura segura,
        gira la muñeca al otro rotulador (interpolando en el aire) y sigue."""
        ordenes, camino, trazo_de, pluma_de, duracion, base = [], [], [], [], 0.0, 0
        for pluma, trazos in grupos:
            self.mapa = self.mapas[pluma]
            dx, dy = self.desplazar.get(pluma, (0.0, 0.0))
            if dx or dy:
                trazos = [[[x + dx, y + dy] for x, y in t] for t in trazos]
            o, c, t, d = self.planificar(trazos, en_el_aire)
            if ordenes:                            # cambio de rotulador, en el aire, en dos pasos
                a, b = ordenes[-1], o[0]
                girado = dict(a)
                girado["wrist_roll.pos"] = b["wrist_roll.pos"]   # 1) solo gira la muñeca, sin moverse
                for (ini, fin, seg) in ((a, girado, 1.5), (girado, b, 2.0)):   # 2) se mueve encima del rojo
                    n = int(seg * self.a.hz)
                    for i in range(1, n + 1):
                        s_ = i / n
                        s_ = s_ * s_ * (3 - 2 * s_)
                        ordenes.append({k: ini[k] + (fin[k] - ini[k]) * s_ for k in fin})
                        camino.append(camino[-1])
                        trazo_de.append(trazo_de[-1])
                        pluma_de.append(pluma)
                    duracion += seg
                print(f"   cambio de rotulador a {pluma}: gira la muñeca y luego se coloca encima")
            ordenes += o
            camino += list(c)
            trazo_de += list(np.asarray(t) + base)
            pluma_de += [pluma] * len(o)
            duracion += d
            base += len(trazos)
        return ordenes, np.array(camino), np.array(trazo_de), pluma_de, duracion

    def planificar(self, trazos_mm, en_el_aire=False):
        """Devuelve (órdenes, camino xyz, nº de trazo de cada orden, duración en s)."""
        a = self.a
        z_pintar = a.z_viaje if en_el_aire else a.z_pintar
        vertices = construir_recorrido(trazos_mm, z_pintar, a.z_viaje, a.z_segura, a.vel, a.vel_viaje,
                                       a.z_aprox, a.vel_aprox)
        pts, vmax = redondear_y_muestrear(vertices, a.radio_esquinas)
        v, ds = perfil_velocidad(pts, vmax, a.acel, acel_lateral=a.acel_curva)
        camino, duracion = muestrear_en_tiempo(pts, v, ds, a.hz)
        camino, t_pausas = insertar_pausas(camino, z_pintar, a.pausa_bajar, a.pausa_subir, a.hz,
                                           a.pausa_esquina)
        duracion += t_pausas
        camino, t_arriba = pausas_arriba(camino, a.pausa_arriba, a.hz)
        duracion += t_arriba
        # nº de trazo en cada instante: cuenta las veces que la punta llega al papel
        tocando = camino[:, 2] <= z_pintar + 0.05
        llegadas = np.r_[tocando[0], tocando[1:] & ~tocando[:-1]]
        trazo_de = np.clip(np.cumsum(llegadas), 1, len(trazos_mm)).astype(int)
        ordenes = []
        for x, y, z in camino:
            x = min(max(x, 0.0), self.mapa.ancho)
            y = min(max(y, 0.0), self.mapa.alto)
            try:
                ordenes.append(self.mapa.articulaciones(x, y, z))
            except ValueError:
                raise FueraDeAlcance(f"El dibujo sale del alcance del robot (punto {x:.0f}, {y:.0f} mm "
                                     f"a {z:.0f} mm de altura).")
        holgura = dict(zip(G.MOTORES, (float(v) for v in a.holgura.split(","))))
        ordenes = compensar_holgura(ordenes, holgura, a.hz)
        ordenes = anticipar(ordenes, self.retraso, a.hz)
        saltos = [max(abs(p[k] - q[k]) for k in G.MOTORES) for p, q in zip(ordenes, ordenes[1:])]
        salto_max = max(saltos) if saltos else 0.0
        if salto_max > 2.0:
            raise FueraDeAlcance(f"El recorrido tiene un salto brusco de {salto_max:.1f}° entre órdenes.")
        return ordenes, camino, trazo_de, duracion


# ======================================================================
# Brazo: real o simulado, con la misma interfaz
# ======================================================================

class BrazoSimulado:
    def __init__(self, hz):
        self.hz = hz

    def ejecutar(self, ordenes, parar, al_avanzar):
        ritmo = Ritmo(self.hz)
        for i in range(len(ordenes)):
            if parar.is_set():
                return i
            if i % 10 == 0:
                al_avanzar(i)
            ritmo.esperar()
        return None

    def levantar(self, destino):
        time.sleep(0.5)

    def ir_a_reposo(self):
        time.sleep(0.5)

    def cerrar(self):
        pass


class BrazoReal:
    def __init__(self, a, mapa):
        from robot_utils import activar_par, conectar, leer_articulaciones, suavizar_motores
        self.leer = leer_articulaciones
        self.hz = a.hz
        reloj_preciso()
        self.robot = conectar(a.port, a.id, rigidez=a.rigidez)
        activar_par(self.robot)
        suavizar_motores(self.robot, a.suavidad_motor)
        self.reposo = mapa.articulaciones(mapa.ancho / 2, mapa.alto / 2, a.z_segura)

    def ir_a_reposo(self):
        ir_suave(self.robot, self.reposo, 3.0, self.hz, vigilar=False)

    def ejecutar(self, ordenes, parar, al_avanzar):
        """Devuelve None si termina, o el índice de la orden en la que se paró."""
        ir_suave(self.robot, ordenes[0], 3.0, self.hz, vigilar=False)
        ritmo = Ritmo(self.hz)
        for i, o in enumerate(ordenes):
            if parar.is_set():
                self.robot.send_action(self.leer(self.robot))  # quieto donde está
                return i
            self.robot.send_action(o)
            if i % 10 == 0:
                al_avanzar(i)
            ritmo.esperar()
        return None

    def levantar(self, destino):
        ir_suave(self.robot, destino, 1.5, self.hz, vigilar=False)

    def cerrar(self):
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para desconectar...")
        self.robot.disconnect()


# ======================================================================
# Trabajos: un dibujo cada vez, en segundo plano
# ======================================================================

class Taller:
    def __init__(self, a, mapas, brazo):
        self.a, self.mapas, self.brazo = a, mapas, brazo
        self.plumas = list(mapas)
        self.mapa = mapas[self.plumas[0]]
        self.plan = Planificador(mapas, a)
        self.parar = threading.Event()
        self.cerrojo = threading.Lock()
        self.estado = {"estado": "libre", "trazo_actual": 0, "trazos_total": 0, "progreso": 0.0,
                       "error": None, "mensaje": "Listo para dibujar.", "trabajo": None,
                       "simulado": a.simular, "conectado": brazo is not None}
        os.makedirs(a.carpeta, exist_ok=True)

    def _poner(self, **kw):
        with self.cerrojo:
            self.estado.update(kw)

    def ver_estado(self):
        """Copia del estado. Si el dibujo ya acabó, lo devuelve una vez y vuelve a 'libre'."""
        with self.cerrojo:
            copia = dict(self.estado)
            if self.estado["estado"] in FINALES:
                self.estado.update(estado="libre", mensaje="Listo para dibujar.")
            return copia

    def preparar(self, cuerpo):
        """SVG → trazos → órdenes, sin mover nada. ValueError (SVG) o FueraDeAlcance."""
        svg = cuerpo.get("svg")
        if not isinstance(svg, str) or "<svg" not in svg:
            raise ValueError("El dibujo no es válido. Genera otro.")
        o = cuerpo.get("opciones") or {}
        op = Opciones(ancho=self.mapa.ancho, alto=self.mapa.alto,
                      tamano=float(o.get("tamano", self.a.tamano)),
                      girar=int(o.get("girar", self.a.girar)),
                      espejo=bool(o.get("espejo", self.a.espejo)),
                      min_detalle=float(o.get("min_detalle", self.a.min_detalle)),
                      punto_max=float(o.get("punto_max", self.a.punto_max)))
        try:
                trazos, informe = svg_a_trazos(svg, op)
        except Exception as e:
            raise ValueError(f"El dibujo no es válido. Genera otro. ({e})")
        if informe["puntos"] > 5000:
            raise ValueError("El dibujo tiene demasiados detalles. Genera otro más sencillo.")
        forzado = o.get("color") or (self.plumas[0] if o.get("un_color") else None)
        if forzado and forzado not in self.plumas:
            raise ValueError(f"No hay rotulador '{forzado}'. Hay: {', '.join(self.plumas)}.")
        # agrupar por rotulador, manteniendo el orden (svg_a_trazos ya deja juntos los del mismo color)
        grupos = []
        for t in trazos:
            pluma = forzado or elegir_pluma(t.color, self.plumas)
            if not grupos or grupos[-1][0] != pluma:
                grupos.append((pluma, []))
            grupos[-1][1].append(a_lista([t])[0])
        # mismo rotulador en grupos separados: juntarlos para no cambiar de color de más
        juntos = {}
        for pluma, ts in grupos:
            juntos.setdefault(pluma, []).extend(ts)
        grupos = list(juntos.items())
        lista = [t for _, ts in grupos for t in ts]
        en_el_aire = bool(o.get("en_el_aire", False))
        ordenes, camino, trazo_de, pluma_de, duracion = self.plan.planificar_colores(grupos, en_el_aire)
        informe.update({"duracion_s": round(duracion, 1), "en_el_aire": en_el_aire,
                        "rotuladores": [p for p, _ in grupos], "cambios_de_color": len(grupos) - 1})
        self._pluma_de = pluma_de
        return svg, lista, informe, ordenes, camino, trazo_de

    def empezar(self, cuerpo):
        if self.brazo is None:
            raise SinRobot("El robot no está conectado.")
        with self.cerrojo:
            if self.estado["estado"] in OCUPADO:
                raise Ocupado("El robot ya está dibujando.")
            self.estado.update(estado="planificando", progreso=0.0, trazo_actual=0, error=None,
                               mensaje="Planificando…")
        try:
            svg, lista, informe, ordenes, camino, trazo_de = self.preparar(cuerpo)
        except Exception:
            self._poner(estado="libre", mensaje="Listo para dibujar.")
            raise
        nombre = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        titulo = re.sub(r"[^a-z0-9]+", "-", str(cuerpo.get("titulo", "")).lower()).strip("-")[:40]
        if titulo:
            nombre += "_" + titulo
        self.parar.clear()
        self._poner(estado="dibujando", trabajo=nombre, inicio=time.time(), trazos_total=informe["trazos"],
                    trazo_actual=1, duracion_s=informe["duracion_s"], mensaje="Dibujando…")
        pluma_de = self._pluma_de
        hilo = threading.Thread(target=self._dibujar,
                                args=(nombre, svg, lista, informe, ordenes, camino, trazo_de, pluma_de), daemon=True)
        hilo.start()
        return nombre, informe

    def _levantar_y_reposo(self, camino, i, pluma_de=None):
        i = min(i, len(camino) - 1)
        x, y, _ = camino[i]
        mapa = self.mapas[pluma_de[i]] if pluma_de else self.mapa
        try:
            self.brazo.levantar(mapa.articulaciones(x, y, self.a.z_segura))
        except ValueError:
            print("No puedo levantar desde aquí; lo dejo quieto.")
            return
        self.brazo.ir_a_reposo()

    def _dibujar(self, nombre, svg, lista, informe, ordenes, camino, trazo_de, pluma_de):
        t0 = time.time()
        resultado = "terminado"
        n = len(ordenes)

        def al_avanzar(i):
            self._poner(progreso=round(i / n, 3), trazo_actual=int(trazo_de[min(i, n - 1)]))
        try:
            print(f"\n>>> Dibujando {nombre}: {informe['trazos']} trazos, ~{informe['duracion_s']:.0f} s, "
                  f"rotuladores {' -> '.join(informe['rotuladores'])}"
                  f"{' (EN EL AIRE)' if informe['en_el_aire'] else ''}"
                  f"{'  [SIMULADO]' if self.a.simular else ''}")
            parado_en = self.brazo.ejecutar(ordenes, self.parar, al_avanzar)
            if parado_en is not None:
                resultado = "parado"
                self._poner(estado="parando", mensaje="Parando: levantando el rotulador…")
                self._levantar_y_reposo(camino, parado_en, pluma_de)
                print("!!! Parado.")
                self._poner(estado="parado", mensaje="Dibujo detenido.")
            else:
                self.brazo.ir_a_reposo()
                print(f"<<< Terminado en {time.time() - t0:.0f} s")
                self._poner(estado="terminado", progreso=1.0, trazo_actual=informe["trazos"], mensaje="Terminado.")
        except Exception as e:
            resultado = f"error: {e}"
            traceback.print_exc()
            try:
                self._levantar_y_reposo(camino, 0)
            except Exception:
                pass
            self._poner(estado="error", error=str(e), mensaje=f"Error del robot: {e}")
        finally:
            with open(os.path.join(self.a.carpeta, nombre + ".svg"), "w", encoding="utf-8") as f:
                f.write(svg)
            with open(os.path.join(self.a.carpeta, nombre + ".json"), "w", encoding="utf-8") as f:
                json.dump({"trabajo": nombre, "resultado": resultado, "segundos": round(time.time() - t0, 1),
                           "simulado": self.a.simular, "informe": informe, "trazos_mm": lista,
                           "parametros": {k: getattr(self.a, k) for k in
                                          ("z_pintar", "z_viaje", "z_segura", "vel", "vel_viaje", "acel",
                                           "radio_esquinas", "hz", "pausa_bajar", "pausa_subir",
                                           "pausa_esquina", "pausa_arriba", "z_aprox", "vel_aprox", "acel_curva", "subida", "holgura", "compensar_subida", "calib", "modelo")}},
                          f, ensure_ascii=False)


# ======================================================================
# HTTP
# ======================================================================

def crear_manejador(taller):
    class Manejador(BaseHTTPRequestHandler):
        def _cabeceras(self, codigo, tipo="application/json"):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo + "; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

        def _json(self, codigo, datos):
            self._cabeceras(codigo)
            self.wfile.write(json.dumps(datos, ensure_ascii=False).encode("utf-8"))

        def _cuerpo(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                raise ValueError("El dibujo es demasiado grande.")
            try:
                return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except json.JSONDecodeError:
                raise ValueError("El dibujo no es válido. Genera otro.")

        def log_message(self, formato, *args):
            if "/estado" not in str(args[0]) and "/status" not in str(args[0]):
                print(f"[web] {formato % args}")

        def do_OPTIONS(self):
            self._cabeceras(204)

        def do_GET(self):
            if self.path.startswith("/status") or self.path.startswith("/estado"):
                self._json(200, taller.ver_estado())
            elif self.path == "/":
                self._cabeceras(200, "text/plain")
                self.wfile.write("Robot pintor en marcha. API: POST /draw · GET /status · POST /stop\n".encode())
            else:
                self._json(404, {"ok": False, "error": "No existe."})

        def do_POST(self):
            try:
                if self.path.startswith("/stop") or self.path.startswith("/parar"):
                    taller.parar.set()
                    self._json(200, {"ok": True})
                    return
                cuerpo = self._cuerpo()
                if self.path.startswith("/preview"):
                    _, lista, informe, _, _, _ = taller.preparar(cuerpo)
                    self._json(200, {"ok": True, "informe": informe, "trazos_mm": lista})
                elif self.path.startswith("/draw"):
                    nombre, inf = taller.empezar(cuerpo)
                    self._json(202, {"ok": True, "trabajo": nombre, "trazos": inf["trazos"],
                                     "longitud_mm": inf["mm_pintando"], "duracion_s": inf["duracion_s"],
                                     "informe": inf})
                else:
                    self._json(404, {"ok": False, "error": "No existe."})
            except SinRobot as e:
                self._json(503, {"ok": False, "error": str(e)})
            except Ocupado as e:
                self._json(409, {"ok": False, "error": str(e)})
            except FueraDeAlcance as e:
                self._json(422, {"ok": False, "error": str(e)})
            except ValueError as e:
                self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                traceback.print_exc()
                self._json(500, {"ok": False, "error": f"Error interno: {e}"})

    return Manejador


def vigilar_teclado(taller):
    """Cualquier tecla en esta ventana = parada de emergencia."""
    while True:
        if msvcrt and msvcrt.kbhit():
            msvcrt.getwch()
            if taller.estado["estado"] == "dibujando":
                print("\n!!! TECLA PULSADA: parada de emergencia")
                taller.parar.set()
        time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser(description="Servidor HTTP del robot pintor")
    ap.add_argument("--simular", action="store_true", help="no conecta con el robot")
    ap.add_argument("--port", default="COM5", help="puerto del follower")
    ap.add_argument("--id", default="follower_pintor")
    ap.add_argument("--puerto-web", type=int, default=8000)
    ap.add_argument("--calib", default="calib_activa.json")
    ap.add_argument("--modelo", default="modelo_brazo.json")
    ap.add_argument("--rotuladores", nargs="*", default=None,
                    help="varios rotuladores: nombre=calib.json,modelo.json  (p.ej. "
                         "azul=calib_color1.json,modelo_color1.json rojo=calib_color2.json,modelo_color2.json). "
                         "El primero es el de los trazos negros.")
    ap.add_argument("--carpeta", default="trabajos", help="dónde se guarda cada dibujo")
    # dibujo
    ap.add_argument("--tamano", type=float, default=130.0, help="lado máximo del dibujo en mm")
    ap.add_argument("--min-detalle", type=float, default=8.0, help="mm mínimos de ojos y detalles cerrados")
    ap.add_argument("--punto-max", type=float, default=6.5, help="mm: formas cerradas más pequeñas se pintan como punto")
    ap.add_argument("--girar", type=int, default=0, help="0, 90, 180 o 270")
    ap.add_argument("--espejo", action="store_true")
    # movimiento (los valores que mejor nos han ido el 21/11)
    ap.add_argument("--z-pintar", type=float, default=0.0,
                    help="mm sobre el papel al pintar. Con nivelación: 0 = tocando justo, -0.5 = aprieta un poco")
    ap.add_argument("--z-viaje", type=float, default=45.0)
    ap.add_argument("--z-segura", type=float, default=70.0)
    ap.add_argument("--vel", type=float, default=30.0)
    ap.add_argument("--vel-viaje", type=float, default=60.0)
    ap.add_argument("--acel", type=float, default=200.0)
    ap.add_argument("--radio-esquinas", type=float, default=0.0)
    ap.add_argument("--hz", type=float, default=100.0)
    ap.add_argument("--pausa-bajar", type=float, default=0.08)
    ap.add_argument("--pausa-subir", type=float, default=0.03)
    ap.add_argument("--pausa-esquina", type=float, default=0.0)
    ap.add_argument("--pausa-arriba", type=float, default=0.0, help="s quieto arriba: levanta, se traslada, baja")
    ap.add_argument("--acel-curva", type=float, default=15.0,
                    help="mm/s² en curvas: más bajo = círculos pequeños más despacio y redondos")
    ap.add_argument("--z-aprox", type=float, default=4.0, help="mm finales de bajada (y primeros de subida) despacio")
    ap.add_argument("--vel-aprox", type=float, default=10.0, help="mm/s en ese tramo")
    ap.add_argument("--compensar-subida", type=float, default=0.0)
    ap.add_argument("--holgura", default="0,0,0,0", help="grados de base,hombro,codo,muñeca")
    ap.add_argument("--retraso", default="", help="archivo de medir_retraso.py (se usa si existe)")
    ap.add_argument("--desplazar", nargs="*", default=[],
                    help="corregir dónde cae un rotulador: nombre=dx,dy en mm (p.ej. rojo=0,-3). "
                         "dx + = hacia c16, dy + = hacia f10")
    ap.add_argument("--mapa", choices=["residuo", "tabla"], default="tabla")
    ap.add_argument("--subida", choices=["hombro", "modelo"], default="hombro",
                    help="hombro: sube con el hombro y la muñeca compensa · modelo: como antes")
    ap.add_argument("--rigidez", type=int, default=16)
    ap.add_argument("--suavidad-motor", type=int, default=20)
    a = ap.parse_args()

    defs = []
    for item in (a.rotuladores or [f"unico={a.calib},{a.modelo}"]):
        try:
            nombre, rutas = item.split("=", 1)
            calib, modelo = rutas.split(",", 1)
        except ValueError:
            raise SystemExit(f"--rotuladores: '{item}' tiene que ser nombre=calib.json,modelo.json")
        defs.append((nombre.strip().lower(), calib.strip(), modelo.strip()))
    mapas = {}
    for nombre, calib, modelo in defs:
        if os.path.exists(calib) and os.path.exists(modelo):
            print(f"Rotulador {nombre}: {calib} + {modelo}")
            mapas[nombre] = MapaHibrido(calib, modelo, compensar_subida=a.compensar_subida, modo=a.mapa,
                                        subida=a.subida)
        elif a.simular:
            print(f"[aviso] No encuentro {calib} / {modelo}: simulo sin comprobar alcance.")
            mapas[nombre] = MapaSimulado()
        else:
            raise SystemExit(f"Faltan {calib} o {modelo}.")
    mapa = mapas[defs[0][0]]
    for n, m in mapas.items():
        if (round(m.ancho), round(m.alto)) != (round(mapa.ancho), round(mapa.alto)):
            raise SystemExit(f"El rotulador {n} tiene otra zona de dibujo ({m.ancho:.0f}×{m.alto:.0f} mm).")

    if a.simular:
        brazo = BrazoSimulado(a.hz)
    else:
        try:
            brazo = BrazoReal(a, mapa)
            input("\nPulsa Enter y el brazo subirá a la posición de reposo (centro de la hoja, en alto)...")
            brazo.ir_a_reposo()
        except Exception as e:
            print(f"\n[ERROR] No he podido conectar con el robot: {e}\n"
                  "El servidor arranca igual: la web dirá 'El robot no está conectado'.")
            brazo = None

    taller = Taller(a, mapas, brazo)
    for item in a.desplazar:
        try:
            nombre, v = item.split("=", 1)
            dx, dy = (float(x) for x in v.split(","))
        except ValueError:
            raise SystemExit(f"--desplazar: '{item}' tiene que ser nombre=dx,dy")
        taller.plan.desplazar[nombre.strip().lower()] = (dx, dy)
        print(f"Rotulador {nombre}: se desplaza {dx:+.1f} mm (hacia c16) y {dy:+.1f} mm (hacia f10)")
    threading.Thread(target=vigilar_teclado, args=(taller,), daemon=True).start()
    servidor = ThreadingHTTPServer(("127.0.0.1", a.puerto_web), crear_manejador(taller))
    print(f"\nRobot pintor escuchando en http://localhost:{a.puerto_web}"
          f"{'  [SIMULADO]' if a.simular else ''}   (zona de dibujo {mapa.ancho:.0f}×{mapa.alto:.0f} mm, "
          f"rotuladores: {', '.join(mapas)})")
    print("Parada de emergencia: CUALQUIER TECLA en esta ventana.  Ctrl+C para cerrar el servidor.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando...")
        taller.parar.set()
        time.sleep(0.5)
    finally:
        servidor.server_close()
        if brazo is not None:
            brazo.cerrar()


if __name__ == "__main__":
    main()
