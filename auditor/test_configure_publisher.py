import pathlib
import tempfile
import unittest
from unittest import mock

import configure_publisher


def app_response():
    return {
        "id": 12345,
        "slug": "tine-plugin-registry-publisher",
        "owner": {"login": "martinkoutecky"},
        "permissions": dict(configure_publisher.EXPECTED_PERMISSIONS),
        "events": [],
    }


def installation_response(selection="selected"):
    return [{
        "id": 67890,
        "account": {"login": "martinkoutecky"},
        "repository_selection": selection,
        "permissions": dict(configure_publisher.EXPECTED_PERMISSIONS),
    }]


class ConfigurePublisherTests(unittest.TestCase):
    def responses(self, installations=None, repositories=None):
        values = {
            "https://api.github.com/app": app_response(),
            "https://api.github.com/app/installations?per_page=100": installations or installation_response(),
            "https://api.github.com/installation/repositories?per_page=100": repositories or {
                "total_count": 1,
                "repositories": [{
                    "full_name": configure_publisher.REPOSITORY,
                    "permissions": {"pull": True, "push": True, "admin": False},
                }],
            },
        }
        return lambda url, _method, _bearer: values[url]

    def test_discovers_only_an_exactly_scoped_installation(self):
        with mock.patch.object(configure_publisher, "app_jwt", return_value="jwt"):
            with mock.patch.object(
                configure_publisher,
                "installation_credentials",
                return_value=("token", dict(configure_publisher.EXPECTED_PERMISSIONS), None),
            ):
                with mock.patch.object(configure_publisher, "github_json", side_effect=self.responses()):
                    self.assertEqual(
                        configure_publisher.discover_installation(12345, "tine-plugin-registry-publisher", pathlib.Path("key")),
                        67890,
                    )

    def test_rejects_all_repository_or_multi_repository_scope(self):
        with mock.patch.object(configure_publisher, "app_jwt", return_value="jwt"):
            with mock.patch.object(
                configure_publisher,
                "installation_credentials",
                return_value=("token", dict(configure_publisher.EXPECTED_PERMISSIONS), None),
            ):
                with mock.patch.object(
                    configure_publisher,
                    "github_json",
                    side_effect=self.responses(installations=installation_response("all")),
                ):
                    with self.assertRaisesRegex(RuntimeError, "selected repositories"):
                        configure_publisher.discover_installation(12345, "tine-plugin-registry-publisher", pathlib.Path("key"))
                with mock.patch.object(
                    configure_publisher,
                    "github_json",
                    side_effect=self.responses(repositories={"total_count": 2, "repositories": []}),
                ):
                    with self.assertRaisesRegex(RuntimeError, "exactly one repository"):
                        configure_publisher.discover_installation(12345, "tine-plugin-registry-publisher", pathlib.Path("key"))

    def test_rendered_config_contains_ids_but_no_token(self):
        with tempfile.TemporaryDirectory() as temp:
            rendered = configure_publisher.render_config(12345, 67890, pathlib.Path(temp) / "key.pem").decode()
            self.assertIn("github_app_id = 12345", rendered)
            self.assertIn("github_installation_id = 67890", rendered)
            self.assertNotIn("token", rendered.lower())


if __name__ == "__main__":
    unittest.main()
