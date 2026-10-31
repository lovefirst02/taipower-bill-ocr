import unittest

import numpy as np

try:
    import cv2  # noqa: F401
except ImportError:
    cv2 = None

if cv2 is not None:
    from taipower_bill_ocr.onnx_ocr import _BILL_DET_SIDE, _DET_MAX_SIDE, BILL_DETECTOR, OnnxOcrEngine, boxes_from_map


@unittest.skipIf(cv2 is None, "opencv is not installed")
class LargePictureTest(unittest.TestCase):
    def _engine(self, boxes):
        engine = OnnxOcrEngine.__new__(OnnxOcrEngine)
        engine._detector_side = _BILL_DET_SIDE
        engine.calls = []

        def detect(image, side=None):
            engine.calls.append(side)
            return boxes

        engine._detect = detect
        return engine

    def test_a_large_picture_with_large_text_is_looked_at_once(self):
        tall = np.array([[0, 0], [400, 0], [400, 60], [0, 60]], dtype=np.float32)
        engine = self._engine([tall])

        engine._boxes_and_crops(np.zeros((3000, 4000, 3), dtype=np.uint8))

        self.assertEqual(engine.calls, [None])

    def test_a_large_picture_of_tiny_text_is_looked_at_in_full(self):
        low = np.array([[0, 0], [400, 0], [400, 20], [0, 20]], dtype=np.float32)
        engine = self._engine([low])

        engine._boxes_and_crops(np.zeros((3000, 4000, 3), dtype=np.uint8))

        self.assertEqual(engine.calls, [None, _DET_MAX_SIDE])


@unittest.skipIf(cv2 is None, "opencv is not installed")
class BoxesFromMapTest(unittest.TestCase):
    def test_a_marked_line_grows_back_around_its_middle(self):
        probability = np.zeros((100, 200), dtype=np.float32)
        probability[45:55, 40:160] = 0.9

        boxes = boxes_from_map(probability, 200, 100, **BILL_DETECTOR)

        self.assertEqual(len(boxes), 1)
        box = boxes[0]
        self.assertLess(box[0][0], 40)
        self.assertLess(box[0][1], 45)
        self.assertGreater(box[2][0], 159)
        self.assertGreater(box[2][1], 54)

    def test_boxes_come_back_in_the_image_s_own_size(self):
        probability = np.zeros((100, 200), dtype=np.float32)
        probability[45:55, 40:160] = 0.9

        small = boxes_from_map(probability, 200, 100, **BILL_DETECTOR)[0]
        large = boxes_from_map(probability, 400, 200, **BILL_DETECTOR)[0]

        np.testing.assert_allclose(large, small * 2, atol=1)

    def test_a_faint_patch_is_not_a_line(self):
        probability = np.zeros((100, 200), dtype=np.float32)
        probability[45:55, 40:160] = BILL_DETECTOR["box_thresh"] - 0.1

        self.assertEqual(boxes_from_map(probability, 200, 100, **BILL_DETECTOR), [])


if __name__ == "__main__":
    unittest.main()
