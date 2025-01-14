import pytest
from click.testing import CliRunner

from esque import config
from esque.cli.commands import ping
from esque.controller.topic_controller import TopicController


@pytest.mark.integration
def test_smoke_test_ping(non_interactive_cli_runner: CliRunner):
    result = non_interactive_cli_runner.invoke(ping, catch_exceptions=False)
    assert result.exit_code == 0


@pytest.mark.integration
def test_correct_amount_of_messages(mocker, non_interactive_cli_runner: CliRunner, topic_controller: TopicController):
    delete_topic_mock = mocker.patch.object(TopicController, "delete_topic", mocker.Mock())

    result = non_interactive_cli_runner.invoke(ping, catch_exceptions=False)
    assert result.exit_code == 0
    assert delete_topic_mock.call_count == 1

    ping_topic = topic_controller.get_cluster_topic(config.PING_TOPIC)
    assert ping_topic.watermarks[0].high == 10
