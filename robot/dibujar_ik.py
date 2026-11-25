"""Dibuja con el modelo real del brazo (modelo_brazo.json).

- El rotulador va a la altura que pidas en toda la hoja y siempre con la misma inclinación.
- Todo el dibujo (bajar, pintar, subir, moverse al siguiente trazo) es UN solo movimiento
  continuo: las esquinas se redondean un poco y la velocidad nunca cae a cero salvo al final.
- Antes de mover nada calcula el recorrido entero; si algo queda fuera de alcance, no arranca.

Uso:
  python dibujar_ik.py --port COM5 --id follower_pintor --figura cuadrado --en-el-aire
  python dibujar_ik.py --port COM5 --id follower_pintor --figura cuadrado
"""
import argparse
import json
import math
import os
import time

import numpy as np

import geometria as G
from dibujar import MapaHoja
from robot_utils import activar_par, conectar, leer_articulaciones, suavizar_motores

try:
    import msvcrt
except ImportError:
    msvcrt = None


class ParadaEmergencia(Exception):
    pass


def vaciar_teclado():
    while msvcrt and msvcrt.kbhit():
        msvcrt.getwch()


def comprobar_parada():
    if msvcrt and msvcrt.kbhit():
        vaciar_teclado()
        raise ParadaEmergencia()


def reloj_preciso():
    try:
        import ctypes
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass


class Ritmo:
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


def figuras(ancho, alto):
    cx, cy = ancho / 2, alto / 2
    r = min(ancho, alto) * 0.35
    circulo = [(cx + r * math.cos(2 * math.pi * i / 180), cy + r * math.sin(2 * math.pi * i / 180))
               for i in range(181)]
    estrella = [(cx + r * math.cos(math.pi / 2 + i * 4 * math.pi / 5),
                 cy - r * math.sin(math.pi / 2 + i * 4 * math.pi / 5)) for i in range(6)]
    return {
        "centro": [[(cx, cy)]],
        "esquinas": [[(0, 0)], [(ancho, 0)], [(ancho, alto)], [(0, alto)]],
        "cuadrado": [[(cx - r, cy - r), (cx + r, cy - r), (cx + r, cy + r), (cx - r, cy + r), (cx - r, cy - r)]],
        "circulo": [circulo],
        "estrella": [estrella],
        "circulos": [[(ancho * f + d / 2 * math.cos(2 * math.pi * i / 120), alto / 2 + d / 2 * math.sin(2 * math.pi * i / 120))
                      for i in range(121)] for f, d in ((0.15, 10), (0.32, 20), (0.55, 30), (0.82, 40))],
        "rejilla": [[(0, y), (ancho, y)] for y in np.linspace(0, alto, 6)] +
                   [[(x, 0), (x, alto)] for x in np.linspace(0, ancho, 9)],
    }


# ---------- mapa híbrido: tus puntos para el papel, el modelo para subir y bajar ----------

class MapaHibrido:
    """Sobre el papel usa tus puntos calibrados (curva suave entre ellos), que son la verdad.
    Para subir o bajar usa el modelo del brazo, que sabe cómo moverse en vertical sin inclinarse.
    Así los pequeños errores del modelo no afectan al dibujo."""

    def __init__(self, ruta_calib, ruta_modelo, ruta_correccion="correccion.json", compensar_subida=0.0,
                 modo="tabla", subida="hombro"):
        self.subida = subida   # "hombro": sube con el hombro y la muñeca compensa · "modelo": como antes
        self.compensar_subida = compensar_subida   # mm hacia fuera (lejos de la base) por cada mm que sube
        self.residuo = None
        self.tabla = MapaHoja(ruta_calib)
        self.modelo = G.Modelo(ruta_modelo)
        self.ancho, self.alto = self.tabla.ancho, self.tabla.alto
        self.correccion = None
        with open(ruta_calib, encoding="utf-8") as f:
            dc = json.load(f)
        con_carga = dc.get("con_carga", False)
        # ¿le has enseñado a levantar en todos los puntos? (ensenar_levantar.py)
        self.h_ensenada = None
        # nivelación (nivelar.py): altura real a la que toca la punta, como un plano por la hoja
        self.nivel = dc.get("nivel", {}).get("plano")
        if self.nivel:
            print(f"Nivelación: la punta toca a {self.nivel[0] + self.nivel[1] * 136 + self.nivel[2] * 68:+.1f} mm "
                  "en el centro (corregido).")
        if dc["puntos"] and all(p.get("arriba_medido") for p in dc["puntos"]):
            self.h_ensenada = float(dc.get("altura_arriba_mm", 10.0))
            print(f"Subir y bajar: usando lo que le enseñaste (hasta {self.h_ensenada:.0f} mm).")
        if modo == "residuo":
            self._preparar_residuo()
        if con_carga:
            print("Calibración hecha con el robot sujetándose solo: no hace falta corrección de peso.")
        elif ruta_correccion and os.path.exists(ruta_correccion):
            with open(ruta_correccion, encoding="utf-8") as f:
                c = json.load(f)
            if c["filas"] == self.tabla.rows and c["cols"] == self.tabla.cols:
                self.correccion = c["valores"]
                print(f"Usando la corrección de peso de {ruta_correccion}")

    def _preparar_residuo(self):
        """Modo recomendado: el modelo del brazo da la forma (líneas rectas de verdad) y tus puntos
        solo corrigen lo que el modelo no sabe (peso, pequeños errores). Se interpola la corrección,
        que es pequeña y suave, en vez de los ángulos, que no dan líneas rectas entre puntos."""
        t = self.tabla
        res = {}
        try:
            for (f, c), p in t.puntos.items():
                x, y = c * t.paso, f * t.paso
                q = self.modelo._ik(x, y, 0.0, self.modelo.theta_en(x, y))
                res[(f, c)] = {k: p["abajo"][k] - q[k] for k in G.MOTORES}
        except ValueError as e:
            print(f"[aviso] El modelo no llega a algún punto calibrado ({e}); uso solo la tabla.")
            return
        self.residuo = res
        grande = max(abs(v) for r in res.values() for v in r.values())
        print(f"Mapa: modelo del brazo + corrección de tus {len(res)} puntos (corrección máx {grande:.1f}°).")

    def _res(self, x, y):
        t = self.tabla
        x = min(max(x, 0.0), t.ancho)
        y = min(max(y, 0.0), t.alto)
        fx, fy = x / t.paso, y / t.paso
        c0 = min(int(fx), t.cols - 2)
        f0 = min(int(fy), t.rows - 2)
        tx, ty = fx - c0, fy - f0

        def val(f, c, k):
            return self.residuo[(min(max(f, 0), t.rows - 1), min(max(c, 0), t.cols - 1))][k]
        out = {}
        for k in G.MOTORES:
            filas = [t._cr(*(val(f0 + df, c0 + dc, k) for dc in (-1, 0, 1, 2)), tx) for df in (-1, 0, 1, 2)]
            out[k] = t._cr(*filas, ty)
        return out

    def _articulaciones_residuo(self, x, y, z):
        bx, by = self.modelo.p[0], self.modelo.p[1]
        rx, ry = x - bx, y - by
        n = math.hypot(rx, ry) or 1.0
        avance = self.compensar_subida * max(z, 0.0)
        xz, yz = x + avance * rx / n, y + avance * ry / n
        th0 = self.modelo.theta_en(x, y)
        ultimo = None
        for delta in (0, 3, -3, 6, -6, 10, -10):
            try:
                q = self.modelo._ik(xz, yz, z, th0 + math.radians(delta))
                break
            except ValueError as e:
                ultimo = e
        else:
            raise ultimo
        r = self._res(x, y)
        out = dict(self.tabla._capa(x, y, "abajo"))   # giro de muñeca y pinza
        for k in G.MOTORES:
            out[k] = q[k] + r[k]
        return out

    def _corr(self, x, y):
        """Corrección de peso interpolada suave (misma curva que los puntos de la tabla)."""
        t = self.tabla
        x = min(max(x, 0.0), t.ancho)
        y = min(max(y, 0.0), t.alto)
        fx, fy = x / t.paso, y / t.paso
        c0 = min(int(fx), t.cols - 2)
        f0 = min(int(fy), t.rows - 2)
        tx, ty = fx - c0, fy - f0

        def val(f, c, k):
            f = min(max(f, 0), t.rows - 1)
            c = min(max(c, 0), t.cols - 1)
            return self.correccion[f"{f},{c}"][k]
        out = {}
        for k in G.MOTORES:
            filas = [t._cr(*(val(f0 + df, c0 + dc, k) for dc in (-1, 0, 1, 2)), tx) for df in (-1, 0, 1, 2)]
            out[k] = t._cr(*filas, ty)
        return out

    def _subir_con_hombro(self, base, z):
        """Sube (o baja, z < 0) la punta z mm girando SOLO el hombro, y la muñeca gira lo contrario
        para que el rotulador no cambie de inclinación. La muñeca hace un arco alrededor del hombro:
        cerca del papel es casi vertical."""
        p, s = self.modelo.p, self.modelo.s
        L1, L2, o2, o3 = p[5], p[6], p[10], p[11]
        t2 = s[1] * math.radians(base["shoulder_lift.pos"]) + o2
        t3 = t2 + s[2] * math.radians(base["elbow_flex.pos"]) + o3
        r_muneca = math.hypot(L1 * math.cos(t2) + L2 * math.cos(t3), L1 * math.sin(t2) + L2 * math.sin(t3))
        d = z / r_muneca                                  # radianes que sube el brazo
        out = dict(base)
        out["shoulder_lift.pos"] = base["shoulder_lift.pos"] + math.degrees(d / s[1])
        out["wrist_flex.pos"] = base["wrist_flex.pos"] - math.degrees(d / s[3])
        return out

    def _base_nivelada(self, x, y):
        """Postura 'tocando justo': la calibración subida en vertical (con el modelo, sin girar
        el rotulador) hasta la altura de contacto que mediste con nivelar.py."""
        base = dict(self.tabla._capa(x, y, "abajo"))
        if not self.nivel:
            return base
        a, b, c = self.nivel
        zn = a + b * x + c * y
        th = self.modelo.theta_en(x, y)
        try:
            q0, q1 = self.modelo._ik(x, y, 0.0, th), self.modelo._ik(x, y, zn, th)
        except ValueError:
            return self._subir_con_hombro(base, zn)
        for k in G.MOTORES:
            base[k] += q1[k] - q0[k]
        return base

    def articulaciones(self, x, y, z, corregir=True):
        if self.subida == "hombro":
            base = self._base_nivelada(x, y)
            return base if abs(z) < 1e-9 else self._subir_con_hombro(base, z)
        if self.nivel:
            a, b, c = self.nivel
            z = z + a + b * x + c * y          # z = 0 pasa a ser "tocando justo"
        if self.residuo is not None:
            return self._articulaciones_residuo(x, y, z)
        base = dict(self.tabla._capa(x, y, "abajo"))
        if corregir and self.correccion:
            for k, v in self._corr(x, y).items():
                base[k] += v
        if abs(z) < 1e-9:
            return base
        h = self.h_ensenada
        if h:
            # cerca del papel (y al apretar): línea recta entre tu postura "tocando" y tu postura "encima"
            arriba = self.tabla._capa(x, y, "arriba")
            if z <= h:
                t = z / h
                return {k: base[k] + t * (arriba[k] - base[k]) for k in base}
            # más arriba: desde tu postura "encima", lo que diga el modelo
            base, z0 = dict(arriba), h
        else:
            z0 = 0.0
        # al subir, empujar la punta hacia fuera para que no "recoja" hacia la base y raye
        bx, by = self.modelo.p[0], self.modelo.p[1]
        rx, ry = x - bx, y - by
        n = math.hypot(rx, ry) or 1.0
        avance = self.compensar_subida * (z - z0)
        xz, yz = x + avance * rx / n, y + avance * ry / n
        th0 = self.modelo.theta_en(x, y)
        ultimo = None
        for delta in (0, 3, -3, 6, -6, 10, -10):
            th = th0 + math.radians(delta)
            try:
                q0 = self.modelo._ik(x, y, z0, th)
                qz = self.modelo._ik(xz, yz, z, th)
                break
            except ValueError as e:
                ultimo = e
        else:
            raise ultimo
        out = dict(base)
        for k in G.MOTORES:
            out[k] = base[k] + (qz[k] - q0[k])
        return out


# ---------- planificación del recorrido ----------

Z_APROX = 4.0      # mm: los últimos mm antes de tocar (y los primeros al levantar) van despacio
VEL_APROX = 10.0   # mm/s en ese tramo


def construir_recorrido(trazos, z_pintar, z_viaje, z_segura, vel_pintar, vel_viaje,
                        z_aprox=None, vel_aprox=None):
    """Lista de vértices 3D con la velocidad máxima del tramo que empieza en cada uno.
    Bajar: rápido hasta z_aprox mm sobre el papel y despacio el final (no golpea ni rebota).
    Subir: despacio los primeros z_aprox mm y luego rápido."""
    z_aprox = Z_APROX if z_aprox is None else z_aprox
    vel_aprox = VEL_APROX if vel_aprox is None else vel_aprox
    za = z_pintar + z_aprox
    usar_aprox = z_aprox > 0 and za < z_viaje - 0.5
    v = []  # (x, y, z, vel)
    x0, y0 = trazos[0][0]
    v.append((x0, y0, z_segura, vel_viaje))
    for trazo in trazos:
        xs, ys = trazo[0]
        v.append((xs, ys, z_viaje, vel_viaje))            # encima del inicio del trazo
        if usar_aprox:
            v.append((xs, ys, za, vel_aprox))             # casi tocando: el final de la bajada, despacio
        v.append((xs, ys, z_pintar, vel_pintar))          # tocando
        for (x, y) in trazo[1:]:
            v.append((x, y, z_pintar, vel_pintar))        # pintar
        xe, ye = trazo[-1]
        if usar_aprox:
            v[-1] = (*v[-1][:3], vel_aprox)               # despegar despacio
            v.append((xe, ye, za, vel_viaje))
        v.append((xe, ye, z_viaje, vel_viaje))            # subir
    xe, ye = trazos[-1][-1]
    v.append((xe, ye, z_segura, vel_viaje))
    # quitar vértices repetidos
    limpio = [v[0]]
    for p in v[1:]:
        if math.dist(p[:3], limpio[-1][:3]) > 1e-6:
            limpio.append(p)
        else:
            limpio[-1] = (*limpio[-1][:3], min(limpio[-1][3], p[3]))
    return limpio


def redondear_y_muestrear(vertices, radio, paso=0.25):
    """Convierte la línea quebrada en una curva continua (esquinas redondeadas) muestreada cada `paso` mm."""
    P = [np.array(v[:3], float) for v in vertices]
    vel = [v[3] for v in vertices]
    pts, vmax = [P[0]], [vel[0]]

    def tramo_recto(a, b, vv):
        n = max(1, int(math.ceil(np.linalg.norm(b - a) / paso)))
        for i in range(1, n + 1):
            pts.append(a + (b - a) * i / n)
            vmax.append(vv)

    actual = P[0]
    for i in range(1, len(P) - 1):
        a, b, c = P[i - 1], P[i], P[i + 1]
        lab, lbc = np.linalg.norm(b - a), np.linalg.norm(c - b)
        r = min(radio, lab / 2, lbc / 2)
        if r < 1e-3:
            tramo_recto(actual, b, vel[i - 1])
            actual = b
            continue
        u1, u2 = (b - a) / lab, (c - b) / lbc
        e1, e2 = b - u1 * r, b + u2 * r
        tramo_recto(actual, e1, vel[i - 1])
        n = max(2, int(math.ceil(2 * r / paso)))
        for k in range(1, n + 1):
            t = k / n
            pts.append((1 - t) ** 2 * e1 + 2 * (1 - t) * t * b + t * t * e2)  # curva de Bézier
            vmax.append(min(vel[i - 1], vel[i]))
        actual = e2
    tramo_recto(actual, P[-1], vel[-2])
    return np.array(pts), np.array(vmax)


def perfil_velocidad(pts, vmax, acel, acel_lateral):
    """Velocidad en cada punto: respeta la máxima, frena en curvas cerradas y acelera/frena suave."""
    ds = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    ds = np.maximum(ds, 1e-6)
    n = len(pts)
    v = vmax.astype(float).copy()
    # límite por curvatura
    for i in range(1, n - 1):
        a, b = pts[i] - pts[i - 1], pts[i + 1] - pts[i]
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na < 1e-9 or nb < 1e-9:
            continue
        cosang = np.clip(a @ b / (na * nb), -1, 1)
        ang = math.acos(cosang)
        if ang > 1e-4:
            radio = (na + nb) / 2 / ang
            v[i] = min(v[i], math.sqrt(acel_lateral * radio))
    v[0] = v[-1] = 0.0
    for i in range(1, n):          # acelerar
        v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * acel * ds[i - 1]))
    for i in range(n - 2, -1, -1):  # frenar
        v[i] = min(v[i], math.sqrt(v[i + 1] ** 2 + 2 * acel * ds[i]))
    return v, ds


def muestrear_en_tiempo(pts, v, ds, hz):
    """Posiciones a enviar, una cada 1/hz segundos."""
    s = np.concatenate([[0.0], np.cumsum(ds)])
    # tiempo para recorrer cada tramo con la velocidad media
    vm = np.maximum((v[:-1] + v[1:]) / 2, 0.3)
    t = np.concatenate([[0.0], np.cumsum(ds / vm)])
    tiempos = np.arange(0.0, t[-1], 1.0 / hz)
    tiempos = np.append(tiempos, t[-1])
    sx = np.interp(tiempos, t, s)
    salida = np.stack([np.interp(sx, s, pts[:, k]) for k in range(3)], -1)
    return salida, t[-1]


def anticipar(ordenes, retraso_s, hz):
    """Manda a cada motor sus órdenes con antelación (su retraso medido con medir_retraso.py),
    para que llegue a tiempo. Sin esto, en un círculo pequeño cada motor va por un sitio distinto."""
    if not retraso_s or not any(v > 0 for v in retraso_s.values()):
        return ordenes
    n = len(ordenes)
    salida = [dict(o) for o in ordenes]
    for k, t in retraso_s.items():
        d = int(round(max(t, 0.0) * hz))
        if d == 0 or k not in ordenes[0]:
            continue
        for i in range(n):
            salida[i][k] = ordenes[min(i + d, n - 1)][k]
    return salida


def cargar_retraso(ruta):
    if ruta and os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            r = json.load(f).get("retraso_s", {})
        print("Anticipación por retraso de motores: " + ", ".join(f"{k.split('.')[0]} {v:.2f}s" for k, v in r.items()))
        return r
    return {}


def compensar_holgura(ordenes, holgura, hz, t_cambio=0.12, umbral=0.05):
    """Compensa la holgura de los engranajes.

    holgura: {motor: grados}. Cuando un motor va a cambiar de sentido, antes de arrancar en el
    nuevo sentido se le hace recorrer la holgura (la mitad hacia cada lado), para que el
    engranaje ya esté apretado y no se pierda ese trozo de movimiento.
    Como se conoce todo el recorrido de antemano, el cambio se hace JUSTO ANTES del giro
    (durante la pausa de la esquina si la hay), en t_cambio segundos para que no dé un tirón."""
    if not any(b > 0 for b in holgura.values()):
        return ordenes
    n = len(ordenes)
    salida = [dict(o) for o in ordenes]
    rampa = max(1, int(round(t_cambio * hz)))
    for k, b in holgura.items():
        if b <= 0:
            continue
        q = np.array([o[k] for o in ordenes])
        d = 0
        for i in range(n):                      # sentido inicial: el primer movimiento claro
            if abs(q[i] - q[0]) > umbral:
                d = 1 if q[i] > q[0] else -1
                break
        if d == 0:
            continue
        sentido = np.full(n, d, float)
        ext_v, ext_i = q[0], 0
        for i in range(1, n):
            if (d > 0 and q[i] >= ext_v - 1e-9) or (d < 0 and q[i] <= ext_v + 1e-9):
                ext_v, ext_i = q[i], i              # último punto del extremo (incluye la pausa)
            elif abs(q[i] - ext_v) > umbral:        # ha empezado a volver: cambio de sentido
                d = -d
                sentido[ext_i + 1:] = d             # el nuevo sentido empieza justo tras el extremo
                ext_v, ext_i = q[i], i
        objetivo = sentido * b / 2
        desfase = np.empty(n)
        actual = objetivo[-1]
        for i in range(n - 1, -1, -1):              # de atrás hacia delante: el cambio se anticipa
            actual += float(np.clip(objetivo[i] - actual, -b / rampa, b / rampa))
            desfase[i] = actual
        for i in range(n):
            salida[i][k] = q[i] + desfase[i]
    return salida


def pausas_arriba(camino, pausa, hz, margen=0.5):
    """Se queda quieto arriba al terminar de subir y antes de empezar a bajar, para que el brazo
    primero levante del todo, luego se traslade, y luego baje (sin moverse en diagonal)."""
    if pausa <= 0 or len(camino) < 3:
        return camino, 0.0
    n = int(round(pausa * hz))
    z = camino[:, 2]
    dz = np.diff(z)
    trozos, extra = [], 0
    for i in range(len(camino)):
        trozos.append(camino[i:i + 1])
        if 0 < i < len(camino) - 1:
            llega = dz[i - 1] > 1e-6 and abs(dz[i]) <= 1e-6       # acaba de subir y empieza a ir recto
            sale = abs(dz[i - 1]) <= 1e-6 and dz[i] < -1e-6        # iba recto y empieza a bajar
            if llega or sale:
                trozos.append(np.repeat(camino[i:i + 1], n, axis=0))
                extra += n
    return np.vstack(trozos), extra / hz


def insertar_pausas(camino, z_pintar, pausa_bajar, pausa_subir, hz, pausa_esquina=0.0):
    """Se queda quieto con la punta en el papel:
    - al empezar cada trazo (los motores terminan de bajar antes de arrancar),
    - antes de levantar (no deja cola al final),
    - en cada esquina cerrada (los motores llegan a la esquina antes de cambiar de dirección)."""
    tocando = camino[:, 2] <= z_pintar + 0.05
    nb, ns, ne = (int(round(t * hz)) for t in (pausa_bajar, pausa_subir, pausa_esquina))
    # esquinas: donde la punta casi se para mientras pinta (el perfil de velocidad frena ahí)
    paso = np.r_[np.inf, np.linalg.norm(np.diff(camino[:, :2], axis=0), axis=1)]
    esquina = np.zeros(len(camino), bool)
    if ne:
        en_papel = paso[tocando & np.isfinite(paso)]
        if len(en_papel):
            lento = tocando & (paso < 0.4 * np.percentile(en_papel, 90))
            i = 0
            while i < len(camino):
                if lento[i]:
                    j = i
                    while j + 1 < len(camino) and lento[j + 1]:
                        j += 1
                    dentro = i > 0 and tocando[i - 1] and j + 1 < len(camino) and tocando[j + 1]
                    if dentro:   # no es el principio ni el final del trazo
                        esquina[i + int(np.argmin(paso[i:j + 1]))] = True
                    i = j + 1
                else:
                    i += 1
    trozos, extra = [], 0
    for i in range(len(camino)):
        if tocando[i] and ns and i + 1 < len(camino) and not tocando[i + 1]:
            trozos.append(np.repeat(camino[i:i + 1], ns, axis=0))   # antes de subir
            extra += ns
        trozos.append(camino[i:i + 1])
        if tocando[i] and nb and (i == 0 or not tocando[i - 1]):
            trozos.append(np.repeat(camino[i:i + 1], nb, axis=0))   # recién llegado al papel
            extra += nb
        if esquina[i]:
            trozos.append(np.repeat(camino[i:i + 1], ne, axis=0))   # en la esquina
            extra += ne
    return np.vstack(trozos), extra / hz


# ---------- ejecución ----------

def ir_suave(robot, destino, duracion, hz, vigilar=True):
    origen = leer_articulaciones(robot)
    ritmo = Ritmo(hz)
    n = max(1, int(duracion * hz))
    for i in range(1, n + 1):
        if vigilar:
            comprobar_parada()
        t = i / n
        t = t * t * (3 - 2 * t)
        robot.send_action({k: origen[k] + (destino[k] - origen[k]) * t for k in destino})
        ritmo.esperar()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--id", required=True)
    ap.add_argument("--modelo", default="modelo_brazo.json")
    ap.add_argument("--calib", default="calib_limpia.json", help="tus puntos tocando el papel")
    ap.add_argument("--figura", default="cuadrado",
                    choices=["centro", "esquinas", "cuadrado", "circulo", "circulos", "estrella", "rejilla"])
    ap.add_argument("--trazos", help="JSON con lista de trazos [[[x,y],...],...] en mm (sustituye a --figura)")
    ap.add_argument("--en-el-aire", action="store_true", help="pinta a la altura de viaje (no toca el papel)")
    ap.add_argument("--z-pintar", type=float, default=0.0, help="mm sobre el papel al pintar (negativo = apretar)")
    ap.add_argument("--z-viaje", type=float, default=15.0, help="mm sobre el papel entre trazos")
    ap.add_argument("--z-segura", type=float, default=40.0, help="mm sobre el papel al empezar y terminar")
    ap.add_argument("--vel", type=float, default=20.0, help="mm/s pintando")
    ap.add_argument("--vel-viaje", type=float, default=40.0, help="mm/s entre trazos")
    ap.add_argument("--acel", type=float, default=60.0, help="mm/s² al arrancar y frenar")
    ap.add_argument("--radio-esquinas", type=float, default=2.0, help="mm de redondeo en las esquinas")
    ap.add_argument("--rigidez", type=int, default=16)
    ap.add_argument("--suavidad-motor", type=int, default=20)
    ap.add_argument("--hz", type=float, default=50.0)
    ap.add_argument("--pausa-bajar", type=float, default=0.3, help="s quieto al tocar el papel antes de pintar")
    ap.add_argument("--pausa-subir", type=float, default=0.2, help="s quieto antes de levantar")
    ap.add_argument("--pausa-esquina", type=float, default=0.0, help="s quieto en cada esquina cerrada")
    ap.add_argument("--acel-curva", type=float, default=15.0,
                    help="mm/s² en curvas: más bajo = círculos pequeños más despacio y redondos")
    ap.add_argument("--z-aprox", type=float, default=Z_APROX, help="mm finales de bajada (y primeros de subida) despacio")
    ap.add_argument("--vel-aprox", type=float, default=VEL_APROX, help="mm/s en ese tramo")
    ap.add_argument("--pausa-arriba", type=float, default=0.0,
                    help="s quieto arriba tras levantar y antes de bajar (levanta, se traslada, baja)")
    ap.add_argument("--subida", choices=["hombro", "modelo"], default="hombro",
                    help="hombro: sube con el hombro y la muñeca compensa (recomendado) · modelo: como antes")
    ap.add_argument("--mapa", choices=["residuo", "tabla"], default="tabla",
                    help="residuo: modelo + corrección (líneas rectas). tabla: como antes")
    ap.add_argument("--retraso", default="",
                    help="archivo de medir_retraso.py (si existe, se usa para anticipar las órdenes)")
    ap.add_argument("--holgura", default="0,0,0,0",
                    help="grados de holgura de base,hombro,codo,muñeca (p.ej. 1,0,0,0)")
    ap.add_argument("--compensar-subida", type=float, default=0.0,
                    help="mm hacia fuera por mm de subida (si al subir la punta se va hacia la base y raya)")
    ap.add_argument("--simular", action="store_true", help="solo calcula el recorrido, no mueve el robot")
    args = ap.parse_args()

    M = G.Modelo(args.modelo)
    mapa = MapaHibrido(args.calib, args.modelo, compensar_subida=args.compensar_subida, modo=args.mapa,
                       subida=args.subida)
    ancho, alto = mapa.ancho, mapa.alto
    if args.trazos:
        with open(args.trazos, encoding="utf-8") as f:
            trazos = json.load(f)
    else:
        trazos = figuras(ancho, alto)[args.figura]

    z_pintar = args.z_viaje if args.en_el_aire else args.z_pintar
    vertices = construir_recorrido(trazos, z_pintar, args.z_viaje, args.z_segura, args.vel, args.vel_viaje,
                                   args.z_aprox, args.vel_aprox)
    pts, vmax = redondear_y_muestrear(vertices, args.radio_esquinas)
    v, ds = perfil_velocidad(pts, vmax, args.acel, acel_lateral=args.acel_curva)
    camino, duracion = muestrear_en_tiempo(pts, v, ds, args.hz)
    camino, t_pausas = insertar_pausas(camino, z_pintar, args.pausa_bajar, args.pausa_subir, args.hz,
                                         args.pausa_esquina)
    duracion += t_pausas
    camino, t_arriba = pausas_arriba(camino, args.pausa_arriba, args.hz)
    duracion += t_arriba

    print("Calculando ángulos de todo el recorrido...")
    ordenes = []
    for x, y, z in camino:
        try:
            ordenes.append(mapa.articulaciones(min(max(x, 0), ancho), min(max(y, 0), alto), z))
        except ValueError as e:
            raise SystemExit(f"No arranco: {e}")
    holgura = dict(zip(G.MOTORES, (float(v) for v in args.holgura.split(","))))
    ordenes = compensar_holgura(ordenes, holgura, args.hz)
    ordenes = anticipar(ordenes, cargar_retraso(args.retraso), args.hz)
    saltos = [max(abs(a[k] - b[k]) for k in G.MOTORES) for a, b in zip(ordenes, ordenes[1:])]
    print(f"Modo: {'EN EL AIRE a ' + str(args.z_viaje) + ' mm' if args.en_el_aire else 'PINTANDO'}"
          f" · {len(trazos)} trazos · duración {duracion:.0f} s · salto máximo por orden {max(saltos):.2f}°")
    if max(saltos) > 2.0:
        raise SystemExit("No arranco: el recorrido tiene un salto brusco de ángulos (revisa el modelo).")
    if args.simular:
        print("Simulación terminada: no se ha movido el robot.")
        return

    reloj_preciso()
    robot = conectar(args.port, args.id, rigidez=args.rigidez)
    activar_par(robot)
    suavizar_motores(robot, args.suavidad_motor)
    print("\n*** PARADA DE EMERGENCIA: pulsa CUALQUIER TECLA (por ejemplo ESPACIO) ***"
          if msvcrt else "\n*** PARADA DE EMERGENCIA: pulsa Ctrl+C ***")
    for s in (3, 2, 1):
        print(f"Empieza en {s}...")
        time.sleep(1)
    vaciar_teclado()
    try:
        print("Yendo a la posición de inicio...")
        ir_suave(robot, ordenes[0], 4.0, args.hz)
        print("Dibujando...")
        ritmo = Ritmo(args.hz)
        for o in ordenes:
            comprobar_parada()
            robot.send_action(o)
            ritmo.esperar()
        print("\nTerminado.")
    except (ParadaEmergencia, KeyboardInterrupt):
        robot.send_action(leer_articulaciones(robot))
        print("\n!!! PARADA DE EMERGENCIA: el brazo se ha quedado quieto donde estaba.")
        resp = input("Pulsa Enter para subir el rotulador despacio, o escribe n y Enter para dejarlo así: ")
        if resp.strip().lower() != "n":
            actual = leer_articulaciones(robot)
            q = np.array([[actual[k] for k in G.MOTORES]])
            x, y, z = G.fk(q, M.p, M.s)[0]
            try:
                th = M.theta_en(x, y)
                q0, q1 = M._ik(x, y, z, th), M._ik(x, y, z + args.z_viaje, th)
                destino = dict(actual)
                for k in G.MOTORES:
                    destino[k] = actual[k] + q1[k] - q0[k]
                ir_suave(robot, destino, 2.5, args.hz, vigilar=False)
            except ValueError:
                print("No puedo subir desde aquí; lo dejo quieto.")
    finally:
        input("\n!! SUJETA EL BRAZO: al desconectar puede caer. Pulsa Enter para terminar...")
        robot.disconnect()


if __name__ == "__main__":
    main()
