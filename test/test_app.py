import sys
import unittest
from unittest.mock import Mock, patch

import app


class ApplicationEntryPointTests(unittest.TestCase):
    def test_main_shows_campaign_manager_and_runs_event_loop(self) -> None:
        application = Mock()
        application.exec.return_value = 23

        with (
            patch("app.QApplication", return_value=application) as application_factory,
            patch("app.CampaignManagerWindow") as window_factory,
        ):
            window = window_factory.return_value

            result = app.main()

        application_factory.assert_called_once_with(sys.argv)
        window.show.assert_called_once_with()
        self.assertEqual(result, 23)