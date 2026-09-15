import unittest

from example import add


class AdditionTests(unittest.TestCase):
    def test_addition(self) -> None:
        self.assertEqual(add(2, 3), 5)
