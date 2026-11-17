"""Ultra-wide frames show a round fruit as round at every station."""

import cv2
import numpy as np
from django.test import SimpleTestCase

from . import wide_angle


HEIGHT, WIDTH, FOCAL_RATIO = 1288, 966, 0.376


def _photo_of_sphere(center, radius=3.3):
    """Render what a pinhole camera sees of a sphere at ``center`` (cm)."""
    focal = FOCAL_RATIO * HEIGHT
    x, y = np.meshgrid(np.arange(WIDTH), np.arange(HEIGHT))
    rays = np.stack([
        (x - (WIDTH - 1) / 2) / focal, (y - (HEIGHT - 1) / 2) / focal,
        np.ones_like(x, dtype=float),
    ], axis=-1)
    rays /= np.linalg.norm(rays, axis=-1, keepdims=True)
    center = np.asarray(center, dtype=float)
    along = rays @ center
    hit = center @ center - along ** 2 <= radius ** 2
    return np.repeat((hit * 255).astype(np.uint8)[..., None], 3, axis=2)


def _outline_ratio(image):
    contours, _ = cv2.findContours(
        (image[..., 0] > 127).astype(np.uint8),
        cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE,
    )
    axes = cv2.fitEllipse(max(contours, key=cv2.contourArea))[1]
    return max(axes) / min(axes)


class WideAngleRectifyTests(SimpleTestCase):
    def test_fruit_at_the_outer_station_becomes_round_again(self):
        photo = _photo_of_sphere((0.0, 22.0, 26.0))
        self.assertGreater(_outline_ratio(photo), 1.25)

        rectified = wide_angle.rectify(photo, FOCAL_RATIO)

        self.assertEqual(rectified.shape, photo.shape)
        self.assertLess(_outline_ratio(rectified), 1.03)

    def test_fruit_at_the_centre_stays_round_and_centred(self):
        photo = _photo_of_sphere((0.0, 0.0, 26.0))

        rectified = wide_angle.rectify(photo, FOCAL_RATIO)

        self.assertLess(_outline_ratio(rectified), 1.03)
        ys, xs = np.nonzero(rectified[..., 0] > 127)
        self.assertAlmostEqual(xs.mean(), (WIDTH - 1) / 2, delta=1.0)
        self.assertAlmostEqual(ys.mean(), (HEIGHT - 1) / 2, delta=1.0)

    def test_both_ends_of_the_long_side_stay_in_frame(self):
        photo = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        photo[:4, WIDTH // 2 - 20:WIDTH // 2 + 20] = 255
        photo[-4:, WIDTH // 2 - 20:WIDTH // 2 + 20] = 255

        rectified = wide_angle.rectify(photo, FOCAL_RATIO)

        self.assertGreater(rectified[:6].max(), 127)
        self.assertGreater(rectified[-6:].max(), 127)

    def test_same_fruit_measures_the_same_area_at_centre_and_outer_station(self):
        areas = []
        for center in ((0.0, 0.0, 26.0), (0.0, 22.0, 26.0), (0.0, -22.0, 26.0)):
            rectified = wide_angle.rectify(_photo_of_sphere(center), FOCAL_RATIO)
            ys, xs = np.nonzero(rectified[..., 0] > 127)
            scale = wide_angle.station_area_scale(
                rectified.shape, FOCAL_RATIO, xs.mean(), ys.mean(),
            )
            areas.append(scale * len(xs))

        self.assertLess(len(xs) / areas[0], 0.75)  # uncompensated outer fruit
        self.assertAlmostEqual(areas[1] / areas[0], 1.0, delta=0.03)
        self.assertAlmostEqual(areas[2] / areas[0], 1.0, delta=0.03)

    def test_area_scale_is_one_without_rectification(self):
        self.assertEqual(
            wide_angle.station_area_scale((HEIGHT, WIDTH, 3), None, 10, 10), 1.0,
        )

    def test_disabled_setting_returns_the_photo_untouched(self):
        photo = _photo_of_sphere((0.0, 22.0, 26.0))

        self.assertIs(wide_angle.rectify(photo, None), photo)

    def test_impossible_focal_ratio_is_rejected_with_the_value(self):
        photo = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)

        with self.assertRaisesRegex(ValueError, 'focal_ratio.*0.01'):
            wide_angle.rectify(photo, 0.01)
