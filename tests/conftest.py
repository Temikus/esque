import random
from concurrent.futures import Future
from pathlib import Path
from string import ascii_letters

import confluent_kafka
import pytest
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka.cimpl import TopicPartition
from pykafka import Producer
from pykafka.exceptions import NoBrokersAvailableError

from esque.cluster import Cluster
from esque.config import Config
from esque.consumergroup import ConsumerGroupController
from esque.errors import raise_for_kafka_error
from esque.topic import Topic, TopicController

TEST_CONFIG = """
[Context]
current = local

[Context.local]
bootstrap_hosts = localhost
bootstrap_port = 9092
security_protocol = PLAINTEXT
"""


def pytest_addoption(parser):
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="run integration tests",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--integration"):
        # --run-e2e given in cli: do not skip e2e tests
        return
    integration = pytest.mark.skip(reason="need --integration option to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(integration)


@pytest.fixture()
def test_config_path(mocker, tmpdir_factory):
    fn: Path = tmpdir_factory.mktemp("config").join("dummy.cfg")
    fn.write_text(TEST_CONFIG, encoding="UTF-8")
    mocker.patch("esque.config.config_path", return_value=fn)
    yield fn


@pytest.fixture()
def test_config(test_config_path):
    config = Config()
    # if os.getenv("ESQUE_TEST_ENV") == "ci":
    #     config.context_switch("ci")
    yield config


@pytest.fixture()
def topic_id(confluent_admin_client) -> str:
    yield "".join(random.choices(ascii_letters, k=5))
    topics = confluent_admin_client.list_topics(timeout=5).topics.keys()
    if topic_id in topics:
        confluent_admin_client.delete_topics([topic_id]).popitem()


@pytest.fixture()
def topic_object(cluster, topic):
    yield TopicController(cluster).get_topic(topic)


@pytest.fixture()
def changed_topic_object(cluster, topic):
    yield TopicController(cluster).get_topic(topic, 1, 3, {"cleanup.policy": "compact"})


@pytest.fixture()
def topic(confluent_admin_client: AdminClient, topic_id: str) -> str:
    """
    Creates a kafka topic consisting of a random 5 character string.

    :return: Topic (str)
    """
    future: Future = confluent_admin_client.create_topics(
        [NewTopic(topic_id, num_partitions=1, replication_factor=1)]
    )[topic_id]
    while not future.done() or future.cancelled():
        if future.result():
            raise RuntimeError
    confluent_admin_client.poll(timeout=1)

    yield topic_id

    topics = confluent_admin_client.list_topics(timeout=5).topics.keys()
    if topic_id in topics:
        confluent_admin_client.delete_topics([topic_id]).popitem()


@pytest.fixture()
def confluent_admin_client(test_config: Config) -> AdminClient:
    admin = AdminClient(test_config.create_confluent_config())
    admin.poll(timeout=5)
    yield admin


@pytest.fixture()
def producer(topic_object: Topic):
    # Send messages synchronously so we can be sure offset has been commited in tests.
    yield Producer(
        topic_object.cluster.pykafka_client.cluster,
        topic_object._pykafka_topic,
        sync=True,
    )


@pytest.fixture()
def consumergroup_controller(cluster: Cluster):
    yield ConsumerGroupController(cluster)


@pytest.fixture()
def consumergroup_instance(
    partly_read_consumer_group: str, consumergroup_controller: ConsumerGroupController
):
    yield consumergroup_controller.get_consumergroup(partly_read_consumer_group)


@pytest.fixture()
def consumer_group():
    yield "".join(random.choices(ascii_letters, k=5))


@pytest.fixture()
def consumer(topic_object: Topic, consumer_group):
    _config = Config().create_confluent_config()
    _config.update(
        {
            "group.id": consumer_group,
            "error_cb": raise_for_kafka_error,
            # We need to commit offsets manually once we"re sure it got saved
            # to the sink
            "enable.auto.commit": False,
            "enable.partition.eof": False,
            # We need this to start at the last committed offset instead of the
            # latest when subscribing for the first time
            "default.topic.config": {"auto.offset.reset": "latest"},
        }
    )
    _consumer = confluent_kafka.Consumer(_config)
    _consumer.assign([TopicPartition(topic=topic_object.name, partition=0, offset=0)])
    yield _consumer


@pytest.fixture()
def filled_topic(producer, topic_object):
    for _ in range(10):
        producer.produce("".join(random.choices(ascii_letters, k=5)).encode("utf-8"))
    yield topic_object


@pytest.fixture()
def partly_read_consumer_group(
    consumer: confluent_kafka.Consumer, filled_topic, consumer_group
):
    for i in range(5):
        msg = consumer.consume(timeout=10)[0]
        consumer.commit(msg, asynchronous=False)
    yield consumer_group


@pytest.fixture()
def cluster(test_config):
    try:
        cluster = Cluster()
    except NoBrokersAvailableError as ex:
        print(test_config.bootstrap_servers)
        raise ex

    yield cluster
