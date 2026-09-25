import unittest

from prototype.scripts.summarize_feedback import feedback_source, render_summary


class FeedbackSummaryTests(unittest.TestCase):
    def test_trial_label_and_unknown_origin(self):
        self.assertEqual(feedback_source({"comment": "[AGENT TRIAL UT01 run=abc] evidence"}), "agent")
        self.assertEqual(feedback_source({"comment": "Great songs!"}), "unclassified")
        self.assertEqual(feedback_source({}), "unclassified")
        self.assertEqual(feedback_source({"evaluator_type": "human"}), "human")

    def test_agent_ratings_never_pool_with_human_or_unclassified_ratings(self):
        report = render_summary([
            {"rating": 2, "comment": "[AGENT TRIAL UT01 run=abc] major gaps"},
            {"rating": 4, "comment": "[AGENT TRIAL UT02 run=def] useful"},
            {"rating": 5, "comment": "Enjoyed it", "evaluator_type": "human"},
            {"rating": 1, "comment": "Earlier unclassified entry"},
        ])
        agent, rest = report.split("## Agent trial ratings\n", 1)[1].split("## Confirmed human ratings", 1)
        human, unknown = rest.split("## Unclassified ratings", 1)
        self.assertIn("3.00/5` (2 responses)", agent)
        self.assertIn("5.00/5` (1 responses)", human)
        self.assertIn("1.00/5` (1 responses)", unknown)
        self.assertNotIn("Enjoyed it", agent)
        self.assertNotIn("Earlier unclassified", human)

    def test_empty_and_legacy_scores_do_not_become_current_ratings(self):
        report = render_summary([{"relevance_rating": 4, "evaluator_type": "human"}])
        human = report.split("## Confirmed human ratings", 1)[1].split("## Unclassified ratings", 1)[0]
        self.assertIn("Playlist rating: not measured", human)
        self.assertIn("Historical usefulness: `4.00/5`", human)
        self.assertIn("Total responses: `0`", render_summary([]))
