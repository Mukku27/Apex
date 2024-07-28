# b3rb_ros_line_follower.py

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy, LaserScan
import math
from synapse_msgs.msg import EdgeVectors, TrafficStatus
from rclpy.duration import Duration

QOS_PROFILE_DEFAULT = 10
PI = math.pi
LEFT_TURN = +1.0
RIGHT_TURN = -1.0
TURN_MIN = 0.0
TURN_MAX = 1.0
SPEED_MIN = 0.0
SPEED_MAX = 1.0
SPEED_25_PERCENT = SPEED_MAX / 4
SPEED_50_PERCENT = SPEED_25_PERCENT * 2
SPEED_75_PERCENT = SPEED_25_PERCENT * 3
THRESHOLD_OBSTACLE_VERTICAL = 1.0
THRESHOLD_OBSTACLE_HORIZONTAL = 0.25
TURN_SCALING_FACTOR = 0.25
OBSTACLE_SAFE_DISTANCE = 2.0
RAMP_COOLDOWN_TIME = 5.0
RAMP_SLOPE_THRESHOLD = 0.1
RAMP_DETECTION_COUNT = 5
SHARP_TURN_THRESHOLD = 0.5  # Threshold for detecting sharp turns

class LineFollower(Node):
    def __init__(self):
        super().__init__('line_follower')
        self.subscription_vectors = self.create_subscription(
            EdgeVectors, '/edge_vectors', self.edge_vectors_callback, QOS_PROFILE_DEFAULT)
        self.publisher_joy = self.create_publisher(
            Joy, '/cerebri/in/joy', QOS_PROFILE_DEFAULT)
        self.subscription_traffic = self.create_subscription(
            TrafficStatus, '/traffic_status', self.traffic_status_callback, QOS_PROFILE_DEFAULT)
        self.subscription_lidar = self.create_subscription(
            LaserScan, '/scan', self.lidar_callback, QOS_PROFILE_DEFAULT)
        self.traffic_status = TrafficStatus()
        self.obstacle_detected = False
        self.ramp_detected = False
        self.closest_obstacle_distance = float('inf')
        self.ramp_climbed_time = None
        self.on_ramp = False

    def rover_move_manual_mode(self, speed, turn):
        msg = Joy()
        msg.buttons = [1, 0, 0, 0, 0, 0, 0, 1]
        msg.axes = [0.0, speed, 0.0, turn]
        self.publisher_joy.publish(msg)

    def calculate_speed_and_turn(self, deviation, width, sharp_turn):
        if sharp_turn:
            speed = SPEED_50_PERCENT
            turn = (deviation / width) * TURN_SCALING_FACTOR * 2  # Increase turn for sharp turns
        else:
            speed = SPEED_MAX
            turn = (deviation / width) * TURN_SCALING_FACTOR
        return speed, turn

    def edge_vectors_callback(self, message):
        speed = SPEED_MAX
        turn = TURN_MIN
        vectors = message
        half_width = vectors.image_width / 2
        sharp_turn = False
        flag = 0

        if vectors.vector_count == 0:
            pass
        elif vectors.vector_count == 1:
            deviation = vectors.vector_1[1].x - vectors.vector_1[0].x
            sharp_turn = abs(deviation) > SHARP_TURN_THRESHOLD * vectors.image_width
            speed, turn = self.calculate_speed_and_turn(deviation, vectors.image_width, sharp_turn)
        elif vectors.vector_count == 2:
            middle_x_left = (vectors.vector_1[0].x + vectors.vector_1[1].x) / 2
            middle_x_right = (vectors.vector_2[0].x + vectors.vector_2[1].x) / 2
            middle_x = (middle_x_left + middle_x_right) / 2
            deviation = half_width - middle_x
            sharp_turn = abs(deviation) > SHARP_TURN_THRESHOLD * half_width
            speed, turn = self.calculate_speed_and_turn(deviation, half_width, sharp_turn)

        if self.traffic_status.stop_sign:
            speed = SPEED_MIN
            print("stop sign detected")

        if self.on_ramp:
            speed = SPEED_25_PERCENT
            print("Slowing down after climbing ramp")
            if self.get_clock().now() - self.ramp_climbed_time > Duration(seconds=RAMP_COOLDOWN_TIME):
                self.on_ramp = False

        if self.ramp_detected:
            speed = SPEED_25_PERCENT
            self.ramp_climbed_time = self.get_clock().now()
            self.ramp_detected = False
            self.on_ramp = True
            print("ramp/bridge detected")
            flag = 1

        if flag == 0:
            if self.obstacle_detected:
                speed = SPEED_25_PERCENT
                distance = self.closest_obstacle_distance
                if distance < THRESHOLD_OBSTACLE_VERTICAL:
                    turn = RIGHT_TURN * (1 - distance / THRESHOLD_OBSTACLE_VERTICAL)
                else:
                    turn = LEFT_TURN * (distance / THRESHOLD_OBSTACLE_VERTICAL)
                print("obstacle detected")

        self.rover_move_manual_mode(speed, turn)

    def traffic_status_callback(self, message):
        self.traffic_status = message

    def lidar_callback(self, message):
        self.ramp_detected = False
        self.obstacle_detected = False
        self.closest_obstacle_distance = float('inf')

        ranges = message.ranges
        length = len(ranges)
        ramp_slope_count = 0

        for i in range(length - 1):
            if ranges[i] < float('inf') and ranges[i + 1] < float('inf'):
                slope = (ranges[i + 1] - ranges[i]) / message.angle_increment
                if slope > RAMP_SLOPE_THRESHOLD:
                    ramp_slope_count += 1
                    if ramp_slope_count >= RAMP_DETECTION_COUNT:
                        self.ramp_detected = True
                        break
                else:
                    ramp_slope_count = 0

        shield_vertical = 4
        shield_horizontal = 1
        theta = math.atan(shield_vertical / shield_horizontal)

        ranges = message.ranges[int(length / 4): int(3 * length / 4)]

        length = float(len(ranges))
        front_ranges = ranges[int(length * theta / PI): int(length * (PI - theta) / PI)]
        side_ranges_right = ranges[0: int(length * theta / PI)]
        side_ranges_left = ranges[int(length * (PI - theta) / PI):]

        angle = theta - PI / 2
        for i in range(len(front_ranges)):
            if front_ranges[i] < THRESHOLD_OBSTACLE_VERTICAL:
                self.obstacle_detected = True
                self.closest_obstacle_distance = min(self.closest_obstacle_distance, front_ranges[i])
                return
            angle += message.angle_increment

        side_ranges_left.reverse()
        for side_ranges in [side_ranges_left, side_ranges_right]:
            angle = 0.0
            for i in range(len(side_ranges)):
                if side_ranges[i] < THRESHOLD_OBSTACLE_HORIZONTAL:
                    self.obstacle_detected = True
                    self.closest_obstacle_distance = min(self.closest_obstacle_distance, side_ranges[i])
                    return
                angle += message.angle_increment

        self.obstacle_detected = False

def main(args=None):
    rclpy.init(args=args)
    line_follower = LineFollower()
    rclpy.spin(line_follower)
    line_follower.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()