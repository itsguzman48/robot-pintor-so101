"""Lee los registros internos del motor de giro de muñeca (wrist_roll), sin moverlo.

Uso:
  python leer_muneca.py --port %PORT% --id %ROBOT_ID%
"""
import argparse

from robot_utils import conectar, leer_articulaciones

REGISTROS = ["Present_Position", "Goal_Position", "Homing_Offset",
             "Min_Position_Limit", "Max_Position_Limit", "Torque_Limit", "Present_Load"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--id", required=True)
    args = ap.parse_args()

    robot = conectar(args.port, args.id)
    try:
        print(f"wrist_roll en grados (LeRobot): {leer_articulaciones(robot)['wrist_roll.pos']:.2f}")
        for r in REGISTROS:
            try:
                try:
                    v = robot.bus.read(r, "wrist_roll", normalize=False)
                except TypeError:
                    v = robot.bus.read(r, "wrist_roll")
                print(f"  {r:20s} {int(v)}")
            except Exception as e:
                print(f"  {r:20s} ? ({e.__class__.__name__})")
        try:
            print(f"  calibración guardada: {robot.calibration['wrist_roll']}")
        except Exception:
            pass
    finally:
        robot.disconnect()


if __name__ == "__main__":
    main()
