"""Utilidades comunes para conectar y leer el SO-101."""


def conectar(port, robot_id, rigidez=None):
    """Conecta con el SO-101 probando los nombres de clase de distintas versiones de LeRobot.

    rigidez: coeficiente P de los motores (LeRobot usa 16). Más alto = cede menos por el peso
    y sigue mejor los movimientos pequeños; demasiado alto puede hacer que vibre.
    """
    extra = {} if rigidez is None else {"position_p_coefficient": rigidez}
    errores = []
    try:
        from lerobot.robots.so101_follower import SO101Follower, SO101FollowerConfig
        robot = SO101Follower(SO101FollowerConfig(port=port, id=robot_id, **extra))
        robot.connect()
        return robot
    except Exception as e:
        errores.append(f"so101_follower: {e!r}")
    try:
        from lerobot.robots.so_follower import SOFollower, SOFollowerRobotConfig
        try:
            cfg = SOFollowerRobotConfig(port=port, id=robot_id, **extra)
        except TypeError:
            try:
                cfg = SOFollowerRobotConfig(robot_type="so101_follower", port=port, id=robot_id, **extra)
            except TypeError:
                print("[aviso] Esta versión de LeRobot no deja cambiar la rigidez; uso la de serie.")
                cfg = SOFollowerRobotConfig(port=port, id=robot_id)
        robot = SOFollower(cfg)
        robot.connect()
        return robot
    except Exception as e:
        errores.append(f"so_follower: {e!r}")
    raise SystemExit("No he podido conectar con el robot:\n" + "\n".join(errores))


def leer_articulaciones(robot):
    """Devuelve {'shoulder_pan.pos': valor, ...} con la posición actual de cada motor."""
    obs = robot.get_observation()
    return {k: float(v) for k, v in obs.items() if k.endswith(".pos")}


def activar_par(robot):
    try:
        robot.bus.enable_torque()
    except Exception as e:
        print(f"[aviso] No he podido activar el par: {e!r}")


def desactivar_par(robot):
    robot.bus.disable_torque()


def suavizar_motores(robot, aceleracion):
    """Baja la aceleración interna de los motores para que filtren los saltos pequeños.

    LeRobot la deja al máximo (254). Con valores bajos (10-40) el motor arranca y frena
    él solo de forma progresiva, y desaparece el temblor de "mover-parar-mover".
    """
    motores = [m for m in robot.bus.motors if m != "gripper"]
    hechos = []
    for m in motores:
        try:
            try:
                robot.bus.write("Acceleration", m, aceleracion, normalize=False)
            except TypeError:
                robot.bus.write("Acceleration", m, aceleracion)
            hechos.append(m)
        except Exception as e:
            print(f"[aviso] No he podido cambiar la aceleración de {m}: {e!r}")
    if hechos:
        print(f"Aceleración interna de los motores: {aceleracion} ({len(hechos)} motores)")
