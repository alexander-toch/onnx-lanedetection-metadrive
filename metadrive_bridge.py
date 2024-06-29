
import os
import glob
from dataclasses import dataclass, field
import cv2
from metadrive import MetaDriveEnv
from metadrive.component.sensors.rgb_camera import RGBCamera
from metadrive.constants import HELP_MESSAGE
from metadrive.component.map.base_map import BaseMap
from metadrive.component.sensors.base_camera import _cuda_enable
from metadrive.component.map.pg_map import MapGenerateMethod
from panda3d.core import Mat4, CSYupRight, CSZupRight, TransformState, UnalignedLMatrix4f

from metadrive_policy.lanedetection_policy_patch_e2e import LaneDetectionPolicyE2E
from metadrive_policy.lanedetection_policy_dpatch import LaneDetectionPolicy

import numpy as np

class Camera:

  K = np.zeros([3, 3])
  R = np.zeros([3, 3])
  t = np.zeros([3, 1])
  P = np.zeros([3, 4])

  def setK(self, fx, fy, px, py):
    self.K[0, 0] = fx
    self.K[1, 1] = fy
    self.K[0, 2] = px
    self.K[1, 2] = py
    self.K[2, 2] = 1.0

  def setR(self, y, p, r):

    Rz = np.array([[np.cos(-y), -np.sin(-y), 0.0], [np.sin(-y), np.cos(-y), 0.0], [0.0, 0.0, 1.0]])
    Ry = np.array([[np.cos(-p), 0.0, np.sin(-p)], [0.0, 1.0, 0.0], [-np.sin(-p), 0.0, np.cos(-p)]])
    Rx = np.array([[1.0, 0.0, 0.0], [0.0, np.cos(-r), -np.sin(-r)], [0.0, np.sin(-r), np.cos(-r)]])
    Rs = np.array([[0.0, -1.0, 0.0], [0.0, 0.0, -1.0], [1.0, 0.0, 0.0]]) # switch axes (x = -y, y = -z, z = x)
    self.R = Rs.dot(Rz.dot(Ry.dot(Rx)))

  def setT(self, XCam, YCam, ZCam):
    X = np.array([XCam, YCam, ZCam])
    self.t = -self.R.dot(X)

  def updateP(self):
    Rt = np.zeros([3, 4])
    Rt[0:3, 0:3] = self.R
    Rt[0:3, 3] = self.t
    self.P = self.K.dot(Rt)

  def __init__(self, config):
    self.config = config
    self.setK(config["fx"], config["fy"], config["px"], config["py"])
    self.setR(np.deg2rad(config["yaw"]), np.deg2rad(config["pitch"]), np.deg2rad(config["roll"]))
    self.setT(config["XCam"], config["YCam"], config["ZCam"])
    self.updateP()


@dataclass
class AttackConfig:
    attack_at_step: int = 6000
    two_pass_attack: bool = False

@dataclass
class Settings:
    seed: int = 1235
    num_scenarios: int = 1
    map_config: str = "SCS"
    headless_rendering: bool = False
    save_images: bool = False
    max_steps: int = 5000
    start_with_manual_control: bool = False
    simulator_window_size: tuple = (1280, 720) # (width, height)
    policy: str = "LaneDetectionPolicyE2E"
    attack_config: AttackConfig | None = field(default_factory=AttackConfig)

class MetaDriveBridge:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.map_config = {
            "config": self.settings.map_config, # S=Straight, C=Circular/Curve
            BaseMap.GENERATE_TYPE: MapGenerateMethod.BIG_BLOCK_SEQUENCE,
            # BaseMap.GENERATE_CONFIG: 3,
            BaseMap.LANE_WIDTH: 3.5,
            BaseMap.LANE_NUM: 2,
        }

        self.policy = LaneDetectionPolicyE2E if self.settings.policy == "LaneDetectionPolicyE2E" else LaneDetectionPolicy

        self.config  = dict(
            use_render=not self.settings.headless_rendering,
            window_size=self.settings.simulator_window_size,
            sensors={
                "rgb_camera": (RGBCamera, self.settings.simulator_window_size[0], self.settings.simulator_window_size[1]),
            },
            vehicle_config={
                "image_source": "rgb_camera",
            },
            agent_policy=self.policy,
            start_seed=self.settings.seed,
            image_on_cuda=True,
            image_observation=True,
            out_of_route_done=True,
            on_continuous_line_done=True,
            crash_vehicle_done=True,
            crash_object_done=True,
            crash_human_done=True,
            traffic_density=0.0,
            map_config=self.map_config,
            num_scenarios=self.settings.num_scenarios,
            decision_repeat=1,
            preload_models=False,
            manual_control=True,
            force_map_generation=True, # disables the PG Map cache
            show_fps=True,
            show_interface_navi_mark=False,
            interface_panel=["dashboard", "rgb_camera"],
        )

    def run(self):
        if self.settings.attack_config is not None:
            self.config["dirty_road_patch_attack_step_index"]= self.settings.attack_config.attack_at_step
            if self.settings.attack_config.two_pass_attack:
                self.run_two_pass_attack()
            else:
                self.config["enable_dirty_road_patch_attack"] = True
                env = MetaDriveEnv(self.config)
                self.run_simulation(env)
        else:
            env = MetaDriveEnv(self.config)
            self.run_simulation(env)

    def run_two_pass_attack(self):
        # ATTACK PASS 1: Drive without attack and generate the patch
        self.config["enable_dirty_road_patch_attack"] = False
        env = MetaDriveEnv(self.config)
        self.run_simulation(env)

        # ATTACK PASS 2: Drive with mounted patch
        env.engine.global_config["enable_dirty_road_patch_attack"] = True
        env.engine.global_config["dirty_road_patch_attack_step_index"] = -1
        self.run_simulation(env)

    def run_simulation(self, env: MetaDriveEnv):
        # delete all the previous camera observations
        for f in glob.glob("./camera_observations/*.jpg"):
            os.remove(f)

        env.reset(self.settings.seed)
        env.current_track_agent.expert_takeover = not self.settings.start_with_manual_control
        
        for i in range(15):
            o, r, tm, tc, infos = env.step([0, 1])
        assert isinstance(o, dict)



        step_index = 0
        while True:
            o, r, tm, tc, info = env.step([0,0])

            if not self.settings.headless_rendering:
                env.render(
                    text={
                        "Auto-Drive (Switch mode: T)": (
                            "on" if env.current_track_agent.expert_takeover else "off"
                        ),
                        "Keyboard Control": "W,A,S,D",
                    }
                )

            if self.settings.save_images:
                if step_index % 20 == 0:
                    cv2.imwrite(
                        f"camera_observations/{str(step_index)}.jpg",
                        (
                            o["image"].get()[..., -1]
                            if env.config["image_on_cuda"]
                            else o["image"][..., -1]
                        )
                        * 255,
                    )

            if tm or tc or step_index >= self.settings.max_steps:
                print(f"Simulation ended at step {step_index}")
                if env.current_seed + 1 < self.settings.seed + self.settings.num_scenarios:
                    env.reset(env.current_seed + 1)
                else:
                    break            
            step_index += 1
