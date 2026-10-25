import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pandas as pd
from cellflow.absorption import solve
from cellflow import queue
from cellflow.reports import _clean_url


class AbsorptionSafety(unittest.TestCase):
    def example(self):
        # Two cells in A, one in B; transient cell has exactly known category fate.
        matrix = np.eye(4)
        matrix[3] = [.1, .2, .2, .5]
        return SimpleNamespace(transition_matrix=matrix), pd.Series(
            pd.Categorical(['A', 'A', 'B', None], categories=['B', 'A', 'unused']))

    def test_category_aggregation_and_order(self):
        names, probs, counts, _ = solve(*self.example())
        self.assertEqual(names, ['B', 'A'])
        self.assertEqual(counts, {'B': 1, 'A': 2})
        np.testing.assert_allclose(probs, [[0, 1], [0, 1], [1, 0], [.4, .6]])

    def test_unreachable_closed_class(self):
        kernel, terms = self.example()
        kernel.transition_matrix[3] = [0, 0, 0, 1]
        with self.assertRaisesRegex(ValueError, 'cannot reach'):
            solve(kernel, terms)

    def test_bad_transition_rejected(self):
        for value in [-.2, np.inf, np.nan, 3.0]:
            kernel, terms = self.example()
            kernel.transition_matrix[3, 0] = value
            with self.assertRaises(ValueError):
                solve(kernel, terms)

    def test_bad_solver_output_never_normalized(self):
        for output in [np.array([[-.1, 1.1]]), np.array([[.2, .2]]),
                       np.array([[np.nan, np.nan]])]:
            with patch('numpy.linalg.solve', return_value=output):
                with self.assertRaises(ValueError):
                    solve(*self.example())

    def test_no_terminal_rejected(self):
        kernel, terms = self.example()
        terms[:] = None
        with self.assertRaises(ValueError):
            solve(kernel, terms)


class IdentitySafety(unittest.TestCase):
    def test_unknown_identity_fails_closed(self):
        with patch.object(queue, '_pid_alive', return_value=True):
            for created in [None, '', 0, 'invalid']:
                with patch.object(queue, '_process_create_time', return_value=None):
                    self.assertFalse(queue._identity_matches(123, created))
            with patch.object(queue, '_process_create_time', return_value=50):
                self.assertFalse(queue._identity_matches(123, 51))
                self.assertTrue(queue._identity_matches(123, 50))

    def test_local_file_image_rejected(self):
        self.assertIsNone(_clean_url('file:///C:/private/image.png', 'img'))
        self.assertEqual(_clean_url('figures/a.png', 'img'), 'figures/a.png')


class PosteriorSafety(unittest.TestCase):
    def test_mean_is_not_zero_uncertainty(self):
        from cellflow.training_diagnostics import posterior_std
        with self.assertRaises(ValueError):
            posterior_std(np.ones((4, 5)), 4, 5, 20)

    def test_both_documented_sample_layouts(self):
        from cellflow.training_diagnostics import posterior_std
        samples = np.random.default_rng(42).normal(size=(20, 4, 5))
        expected = samples.std(axis=0, ddof=1)
        np.testing.assert_allclose(posterior_std(samples, 4, 5, 20), expected)
        np.testing.assert_allclose(posterior_std(samples.transpose(1, 2, 0), 4, 5, 20), expected)
        samples[0, 0, 0] = np.nan
        with self.assertRaises(ValueError):
            posterior_std(samples, 4, 5, 20)
