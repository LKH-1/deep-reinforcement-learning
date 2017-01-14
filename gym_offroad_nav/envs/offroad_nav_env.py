import gym
import cv2
import numpy as np
from gym import error, spaces, utils
from gym.utils import seeding

class OffRoadNavEnv(gym.Env):
    metadata = {'render.modes': ['human']}

    def __init__(self, rewards, vehicle_model):
        self.viewer = None

        # A tf.tensor (or np) containing rewards, we need a constant version and 
        self.rewards = rewards

        self.vehicle_model = vehicle_model

        self.K = 10

        self.state = None

        self.front_view_disp = np.zeros((400, 400, 3), np.uint8)

    def _step(self, action):
        ''' Take one step in the environment
        state is the vehicle state, not the full MDP state including history.

        Parameters
        ----------
        action : Numpy array
            The control input for vehicle. [v_forward, yaw_rate]

        Returns
        -------
        Tuple
            A 4-element tuple (state, reward, done, info)
        '''
        self.state = self.vehicle_model.predict(self.state, action)

        # Y forward, X lateral
        # ix = -19, -18, ...0, 1, 20, iy = 0, 1, ... 39
        x, y = self.state[:2, 0]
        ix, iy = self.get_ixiy(x, y)
        done = (ix < -19) or (ix > 20) or (iy < 0) or (iy > 39)

        reward = self._bilinear_reward_lookup(x, y)

        # debug info
        info = {}

        return self.state, reward, done, info

    def get_linear_idx(self, ix, iy):
        linear_idx = (40 - 1 - iy) * 40 + (ix + 19)
        return linear_idx

    def _get_reward(self, ix, iy):
        linear_idx = self.get_linear_idx(ix, iy)
        r = self.rewards.flatten()[linear_idx]
        return r

    def get_ixiy(self, x, y, scale=1.):
        ix = int(np.floor(x * float(scale) / 0.5))
        iy = int(np.floor(y * float(scale) / 0.5))
        return ix, iy

    def _bilinear_reward_lookup(self, x, y, debug=True):
        ix, iy = self.get_ixiy(x, y)
        # print "(x, y) = ({}, {}), (ix, iy) = ({}, {})".format(x, y, ix, iy)

        x0 = int(np.clip(ix, -19, 20))
        y0 = int(np.clip(iy, 0, 39))
        x1 = int(np.clip(ix + 1, -19, 20))
        y1 = int(np.clip(iy + 1, 0, 39))

        f00 = self._get_reward(x0, y0)
        f01 = self._get_reward(x0, y1)
        f10 = self._get_reward(x1, y0)
        f11 = self._get_reward(x1, y1)

        xx = x / 0.5 - ix
        yy = y / 0.5 - iy

        w00 = (1.-xx)*(1.-yy)
        w01 = yy*(1.-xx)
        w10 = xx*(1.-yy)
        w11 = xx*yy

        r = f00*w00 + f01*w01 + f10*w10 + f11*w11
        '''
        if debug:
            print "reward[{:6.2f},{:6.2f}] = {:7.2f}*{:4.2f} + {:7.2f}*{:4.2f} + {:7.2f}*{:4.2f} + {:7.2f}*{:4.2f} = {:7.2f}".format(
                x, y, f00, w00, f01, w01, f10, w10, f11, w11, r
            ),
        '''
        return r

    def _reset(self, s0):
        if not hasattr(self, "padded_rewards"):
            self.padded_rewards = np.ones((80, 80), dtype=self.rewards.dtype) * np.min(self.rewards)
            self.padded_rewards[20:60, 20:60] = self.rewards

        if not hasattr(self, "R"):
            self.R = self.to_image(self.rewards, self.K)

        if not hasattr(self, "bR"):
            self.bR = self.to_image(self.debug_bilinear_R(self.K), self.K)

        self.disp_img = np.copy(self.bR)
        self.vehicle_model.reset(s0)
        self.state = s0
        return s0

    def to_image(self, R, K, interpolation=cv2.INTER_NEAREST):
        R = (R - np.min(R)) / (np.max(R) - np.min(R)) * 255.
        R = np.clip(R, 0, 255).astype(np.uint8)
        R = cv2.resize(R, (400, 400), interpolation=interpolation)[..., None]
        R = np.concatenate([R, R, R], axis=2)
        return R

    def debug_bilinear_R(self, K):
        X = np.linspace(-10, 10, num=40*K)
        Y = np.linspace(0, 20, num=40*K)

        bR = np.zeros((40*K, 40*K))

        for i, x in enumerate(X):
            for j, y in enumerate(Y):
                bR[-j, i] = self._bilinear_reward_lookup(x, y, debug=False)

        return bR

    def get_front_view(self, state):
        x, y, theta = state[:3, 0]

        ix, iy = self.get_ixiy(x, y)

        iix = np.clip(ix + 19, 0, 39)
        iiy = np.clip(40 - 1 - iy, 0, 39)

        # print "iix = {}, iiy = {}, theta = {}".format(iix, iiy, theta)
        angle = theta * 180. / np.pi
        M = cv2.getRotationMatrix2D((iix + 20, iiy + 20), angle, 1)
        rotated = cv2.warpAffine(self.padded_rewards, M, (80, 80))
        img = cv2.getRectSubPix(rotated, (20, 20), (iix + 20, iiy + 20 - 10))

        assert img.shape == (20, 20), "rotated_with_border.shape = {}, iix = {}, iiy = {}, img.shape = {}".format(rotated_with_border.shape, iix, iiy, img.shape)

        front_view = self.to_image(img, self.K * 2)
        front_view[0, :, :] = 255
        front_view[-1, :, :] = 255
        front_view[:, 1, :] = 255
        front_view[:, -1, :] = 255
        self.front_view_disp = front_view

        return img

    def _render(self, info, mode='human', close=False):

        wnd_name = info["worker"]
        # cv2.namedWindow(wnd_name)

        if self.state is not None:

            x, y = self.state[:2, 0]
            ix, iy = self.get_ixiy(x, y, self.K)
            theta = self.state[2, 0]

            # Turn ix, iy to image coordinate on disp_img (reward)
            x, y = 40*self.K/2-1 + ix, 40*self.K-1-iy

            # Draw vehicle on image without copying it first to leave a trajectory
            cv2.circle(self.disp_img, (x, y), 1, (169, 255, 0), 0)

            # Copy the image and draw again
            disp_img = np.copy(self.disp_img)
            pt1 = (x, y)
            cv2.circle(disp_img, pt1, 2, (0, 0, 255), 2)
            pt2 = (x + int(50 * np.cos(theta - np.pi / 2)), y + int(50 * np.sin(theta - np.pi / 2)))
            cv2.arrowedLine(disp_img, pt1, pt2, (0, 0, 255), tipLength=0.2)

            # Put max/total return on image for debugging
            # text = "max return = {:.3f}".format(info["max_return"])
            # cv2.putText(disp_img, text, (0, 370), cv2.FONT_HERSHEY_PLAIN, 1, (255, 255, 255), 1)
            # text = "total_return = {:.3f}".format(info["total_return"])
            # cv2.putText(disp_img, text, (0, 390), cv2.FONT_HERSHEY_PLAIN, 1, (255, 255, 255), 1)

            cv2.imshow4(2*int(wnd_name[-1]), disp_img)
            cv2.imshow4(2*int(wnd_name[-1]) + 1, self.front_view_disp)
        '''
        else:
            print "\33[93m{} not initialized yet\33[0m".format(wnd_name)
        '''

        # cv2.waitKey(5)
