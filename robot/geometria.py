"""Modelo geométrico (cinemática) del SO-101 sobre la hoja.

Coordenadas de la hoja en mm: x hacia la derecha (columnas), y hacia abajo (filas), z hacia
arriba desde el papel (z = 0 es la superficie del papel).

Parámetros que se ajustan con tus datos (no hace falta medir nada a mano):
  bx, by   posición del eje de la base sobre el plano de la hoja
  psi      hacia dónde mira la base cuando shoulder_pan = 0
  hz       altura del eje del hombro sobre el papel
  d0       distancia horizontal del eje de giro de la base al eje del hombro
  L1, L2   longitud del brazo y del antebrazo
  e        desplazamiento lateral del rotulador (si está pegado a un lado de la pinza)
  pa, pb   posición de la punta del rotulador respecto a la muñeca
  o2,o3,o4 a qué ángulo real corresponde el 0 de hombro, codo y muñeca
Signos s1..s4: hacia qué lado cuenta cada motor.
"""
import itertools
import json
import math

import numpy as np

MOTORES = ["shoulder_pan.pos", "shoulder_lift.pos", "elbow_flex.pos", "wrist_flex.pos"]
NOMBRES = ["bx", "by", "psi", "hz", "d0", "L1", "L2", "e", "pa", "pb", "o2", "o3", "o4"]
# valores orientativos del SO-101 y cuánto se pueden alejar (para que el ajuste no se vuelva loco)
PRIOR = {"d0": (0.0, 40.0), "L1": (116.0, 25.0), "L2": (135.0, 35.0), "e": (0.0, 40.0),
         "pa": (100.0, 80.0), "pb": (0.0, 80.0)}


def fk(q, p, s):
    """Ángulos de motor (grados, array N x 4) -> punta del rotulador (N x 3, mm)."""
    q = np.radians(np.atleast_2d(q))
    bx, by, psi, hz, d0, L1, L2, e, pa, pb, o2, o3, o4 = p
    s1, s2, s3, s4 = s
    H = psi + s1 * q[:, 0]
    t2 = s2 * q[:, 1] + o2
    t3 = t2 + s3 * q[:, 2] + o3
    t4 = t3 + s4 * q[:, 3] + o4
    r = d0 + L1 * np.cos(t2) + L2 * np.cos(t3) + pa * np.cos(t4) - pb * np.sin(t4)
    z = hz + L1 * np.sin(t2) + L2 * np.sin(t3) + pa * np.sin(t4) + pb * np.cos(t4)
    x = bx + r * np.sin(H) + e * np.cos(H)
    y = by + r * np.cos(H) - e * np.sin(H)
    return np.stack([x, y, z], -1)


def angulo_pinza(q, p, s):
    """Ángulo absoluto (rad) de la pinza respecto a la horizontal."""
    q = np.radians(np.atleast_2d(q))
    s1, s2, s3, s4 = s
    o2, o3, o4 = p[10], p[11], p[12]
    return s2 * q[:, 1] + o2 + s3 * q[:, 2] + o3 + s4 * q[:, 3] + o4


def _residuos(p, s, q_pts, xy_pts, q_plano, peso_plano):
    r = []
    t = fk(q_pts, p, s)
    r.append((t[:, 0] - xy_pts[:, 0]).ravel())
    r.append((t[:, 1] - xy_pts[:, 1]).ravel())
    r.append(t[:, 2].ravel())
    if q_plano is not None and len(q_plano):
        r.append(peso_plano * fk(q_plano, p, s)[:, 2])
    for i, n in enumerate(NOMBRES):
        if n in PRIOR:
            m, sd = PRIOR[n]
            r.append(np.array([(p[i] - m) / sd * 3.0]))
    return np.concatenate(r)


def _lm(p, s, args, iters=200):
    lam = 1e-2
    f = _residuos(p, s, *args)
    coste = f @ f
    for _ in range(iters):
        J = np.empty((len(f), len(p)))
        for j in range(len(p)):
            h = 1e-6 * max(1.0, abs(p[j]))
            dp = p.copy()
            dp[j] += h
            J[:, j] = (_residuos(dp, s, *args) - f) / h
        A = J.T @ J
        g = J.T @ f
        mejoro = False
        for _ in range(10):
            paso = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-9), -g)
            pn = p + paso
            fn = _residuos(pn, s, *args)
            cn = fn @ fn
            if cn < coste:
                p, f, coste = pn, fn, cn
                lam = max(lam / 3, 1e-9)
                mejoro = True
                break
            lam *= 4
        if not mejoro or np.abs(paso).max() < 1e-7:
            break
    return p, coste


def _base_inicial(q_pts, xy_pts, s1):
    """Busca dónde está la base mirando hacia dónde apunta el giro de la base en cada punto."""
    pan = np.radians(q_pts[:, 0]) * s1
    mejor = None
    for bx in np.arange(-500, 801, 20):
        for by in np.arange(-500, 701, 20):
            ang = np.arctan2(xy_pts[:, 0] - bx, xy_pts[:, 1] - by)
            dif = ang - pan
            c, sn = np.cos(dif).mean(), np.sin(dif).mean()
            disp = 1 - math.hypot(c, sn)
            dist = np.hypot(xy_pts[:, 0] - bx, xy_pts[:, 1] - by).min()
            if dist < 60:
                continue
            if mejor is None or disp < mejor[0]:
                mejor = (disp, bx, by, math.atan2(sn, c))
    return mejor[1:]


def espejo(p, s):
    """Misma postura reflejada respecto al papel (z -> -z): encaja igual de bien con puntos del papel."""
    p = p.copy()
    p[3], p[9], p[10], p[11], p[12] = -p[3], -p[9], -p[10], -p[11], -p[12]
    return p, (s[0], -s[1], -s[2], -s[3])


def ajustar(q_pts, xy_pts, q_plano=None, peso_plano=None, q_arriba=None, aviso=print):
    """Ajusta el modelo. Devuelve (parámetros, signos, informe).

    q_pts/xy_pts: puntos tocando el papel con su posición conocida en la hoja.
    q_plano: más posturas tocando el papel sin posición conocida (barrido), opcional.
    q_arriba: posturas con el rotulador levantado; solo sirven para saber hacia dónde es "arriba"
              (con datos solo del papel, el modelo no distingue arriba de abajo).
    """
    q_pts = np.asarray(q_pts, float)
    xy_pts = np.asarray(xy_pts, float)
    q_plano = None if q_plano is None or len(q_plano) == 0 else np.asarray(q_plano, float)
    if peso_plano is None:
        peso_plano = 1.0 if q_plano is None else math.sqrt(len(q_pts) / len(q_plano))
    args = (q_pts, xy_pts, q_plano, peso_plano)
    candidatos = []
    for s1 in (1, -1):
        bx, by, psi = _base_inicial(q_pts, xy_pts, s1)
        for s2, s3, s4 in itertools.product((1, -1), repeat=3):
            mejor = None
            for o2 in (math.pi / 2, -math.pi / 2):
                for hz in (40.0, 110.0):
                    p0 = np.array([bx, by, psi, hz, 0.0, 116.0, 135.0, 0.0, 100.0, 0.0,
                                   o2, -s3 * math.pi / 2 if o2 > 0 else s3 * math.pi / 2, 0.0])
                    s = (s1, s2, s3, s4)
                    p, coste = _lm(p0, s, args, iters=60)
                    if mejor is None or coste < mejor[1]:
                        mejor = (p, coste, s)
            candidatos.append(mejor)
    candidatos.sort(key=lambda c: c[1])
    p, coste, s = candidatos[0]
    p, coste = _lm(p, s, args, iters=400)
    if q_arriba is not None and len(q_arriba):
        z_ar = float(np.median(fk(np.asarray(q_arriba, float), p, s)[:, 2]))
        if z_ar < 0:
            p, s = espejo(p, s)  # con datos solo del papel, arriba y abajo salen intercambiables
    else:
        aviso("[aviso] Sin puntos 'arriba' no puedo confirmar hacia dónde es arriba.")
    t = fk(q_pts, p, s)
    err_xy = np.hypot(t[:, 0] - xy_pts[:, 0], t[:, 1] - xy_pts[:, 1])
    informe = {"err_xy_medio": float(err_xy.mean()), "err_xy_max": float(err_xy.max()),
               "err_z_medio": float(np.abs(t[:, 2]).mean()), "err_z_max": float(np.abs(t[:, 2]).max())}
    if q_plano is not None:
        zb = fk(q_plano, p, s)[:, 2]
        informe["barrido_z_medio"] = float(np.abs(zb).mean())
        informe["barrido_z_max"] = float(np.abs(zb).max())
    if q_arriba is not None and len(q_arriba):
        informe["arriba_z_mediana"] = float(np.median(fk(np.asarray(q_arriba, float), p, s)[:, 2]))
    return p, s, informe


class Modelo:
    def __init__(self, ruta):
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        self.p = np.array([d["parametros"][n] for n in NOMBRES])
        self.s = tuple(d["signos"])
        self.theta = d["angulo_pinza"]          # ángulo de la pinza a mantener (rad)
        self.theta_coef = d.get("angulo_pinza_coef")  # si existe: el ángulo cambia suave por la hoja
        self.hoja = d.get("hoja_mm", [1.0, 1.0])
        self.codo = d["codo"]                   # +1 / -1: hacia qué lado dobla el codo
        self.fijos = d["fijos"]                 # wrist_roll y gripper: se dejan como en la calibración
        self.limites = d.get("limites", {})

    def theta_en(self, x, y):
        """Inclinación de la pinza en ese punto de la hoja (como la pusiste al calibrar, pero suave)."""
        if not self.theta_coef:
            return self.theta
        u, v = x / self.hoja[0], y / self.hoja[1]
        return float(np.dot(self.theta_coef, [1, u, v, u * u, v * v, u * v]))

    def ik(self, x, y, z):
        """Punta del rotulador en (x, y, z) mm. La inclinación de la pinza depende solo de (x, y),
        así que al subir o bajar el rotulador se mueve en vertical sin inclinarse."""
        th0 = self.theta_en(x, y)
        ultimo = None
        for delta in (0, 3, -3, 6, -6, 10, -10, 15, -15):  # si no llega, inclina un poco la pinza
            try:
                return self._ik(x, y, z, th0 + math.radians(delta))
            except ValueError as e:
                ultimo = e
        raise ultimo

    def _ik(self, x, y, z, th):
        bx, by, psi, hz, d0, L1, L2, e, pa, pb, o2, o3, o4 = self.p
        s1, s2, s3, s4 = self.s
        dx, dy = x - bx, y - by
        rho = math.hypot(dx, dy)
        r_tip = math.sqrt(max(rho * rho - e * e, 1e-9))
        H = math.atan2(dx, dy) - math.atan2(e, r_tip)
        # punta -> muñeca (la pinza mantiene su ángulo, así que el desplazamiento es fijo)
        rw = r_tip - d0 - (pa * math.cos(th) - pb * math.sin(th))
        zw = z - hz - (pa * math.sin(th) + pb * math.cos(th))
        D = (rw * rw + zw * zw - L1 * L1 - L2 * L2) / (2 * L1 * L2)
        if abs(D) > 1:
            raise ValueError(f"Punto fuera del alcance del brazo: ({x:.0f}, {y:.0f}, {z:.0f}) mm")
        rel = self.codo * math.acos(D)          # ángulo del antebrazo respecto al brazo
        t2 = math.atan2(zw, rw) - math.atan2(L2 * math.sin(rel), L1 + L2 * math.cos(rel))
        t3 = t2 + rel
        q1 = (H - psi) / s1
        q2 = (t2 - o2) / s2
        q3 = (t3 - t2 - o3) / s3
        q4 = (th - t3 - o4) / s4
        q = [math.degrees(v) for v in (q1, q2, q3, q4)]
        # volver al rango -180..180 alrededor de lo calibrado
        out = {}
        for k, v in zip(MOTORES, q):
            ref = self.limites.get(k, [v, v])
            centro = (ref[0] + ref[1]) / 2
            while v - centro > 180:
                v -= 360
            while v - centro < -180:
                v += 360
            out[k] = v
        out.update(self.fijos)
        return out
