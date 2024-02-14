import cv2
from metadrive.policy.base_policy import BasePolicy
from metadrive.engine.engine_utils import get_global_config
from metadrive.obs.image_obs import ImageStateObservation
from metadrive.utils.math import not_zero
from PIL import Image
import numpy as np
import sys, os

sys.path.append(os.path.dirname(os.getcwd()))
from inference import ONNXPipeline
from lanefitting import draw_lane


class LaneDetectionPolicy(BasePolicy):
    MAX_SPEED = 100  # km/h
    NORMAL_SPEED = 30  # km/h
    ACC_FACTOR = 1.0
    DEACC_FACTOR = -5
    DELTA = 10.0  # Exponent of the velocity term

    def __init__(self, control_object, random_seed=None, config=None):
        super(LaneDetectionPolicy, self).__init__(control_object, random_seed, config)
        self.onnx_pipeline = ONNXPipeline()
        self.camera_observation = ImageStateObservation(get_global_config().copy())
        self.target_speed = self.NORMAL_SPEED

    def act(self, agent_id=None):
        action = self.expert()
        self.action_info["action"] = action
        return action

    def expert(self):

        # get RGB camera image from vehicle
        observation = self.camera_observation.observe(self.control_object)
        image = Image.fromarray((observation["image"][..., -1] * 255).astype(np.uint8))
        image_size = (image.width, image.height)    

        # infer steering angle from image
        steering_angle, keypoints = self.onnx_pipeline.infer_steering_angle(image, image_size)

        if steering_angle is None:
            steering_angle = 0

        action = [steering_angle, self.acceleration()]

        # TODO: add a flag to enable image saving
        # lane_image = draw_lane(image, keypoints, image_size)
        # if lane_image is not None:            
        #     print("saving")
        #     random = np.random.randint(0, 100)
        #     cv2.imwrite(
        #         f"lane_{str(random)}.jpg",
        #         lane_image
        #     )

        # TODO: check steering_control() in metadrive.policy.idm_policy

        return action

    def acceleration(self) -> float:
        ego_vehicle = self.control_object
        ego_target_speed = not_zero(self.target_speed, 0)
        acceleration = self.ACC_FACTOR * (
            1 - np.power(max(ego_vehicle.speed_km_h, 0) / ego_target_speed, self.DELTA)
        )
        return acceleration