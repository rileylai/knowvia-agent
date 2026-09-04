import fakeredis
from rq import Queue, SimpleWorker
from rq.registry import ScheduledJobRegistry
from rq.scheduler import RQScheduler

from src.queue import (
    FakeQueueClient,
    QueueRetryPolicy,
    RQQueueClient,
    get_callable_import_path,
)


def sample_task(text: str) -> str:
    return text.upper()


_retry_attempts = 0


def retry_once_task() -> str:
    global _retry_attempts
    _retry_attempts += 1
    if _retry_attempts == 1:
        raise RuntimeError("transient test failure")
    return "ok"


def test_fake_queue_client_enqueue() -> None:
    client = FakeQueueClient()

    enqueued = client.enqueue(
        queue_name="default",
        function=sample_task,
        args=("hello",),
    )

    assert enqueued.queue_name == "default"
    assert enqueued.function_name == get_callable_import_path(sample_task)
    assert enqueued.args == ("hello",)
    assert enqueued.kwargs == {}
    assert enqueued.timeout_seconds is None
    assert len(client.enqueued_jobs) == 1


def test_rq_queue_client_enqueue_with_local_connection() -> None:
    connection = fakeredis.FakeRedis()
    client = RQQueueClient(connection=connection)

    enqueued = client.enqueue(
        queue_name="default",
        function=sample_task,
        args=("hello",),
        description="test job",
        timeout_seconds=321,
    )

    queue = Queue(name="default", connection=connection)
    fetched_job = queue.fetch_job(enqueued.job_id)

    assert fetched_job is not None
    assert fetched_job.description == "test job"
    assert fetched_job.func_name == get_callable_import_path(sample_task)
    assert enqueued.function_name == fetched_job.func_name
    assert fetched_job.args == ("hello",)
    assert fetched_job.timeout == 321
    assert enqueued.timeout_seconds == 321


def test_rq_queue_client_persists_bounded_retry_policy() -> None:
    connection = fakeredis.FakeRedis()
    client = RQQueueClient(connection=connection)

    enqueued = client.enqueue(
        queue_name="telegram",
        function=sample_task,
        args=("hello",),
        retry_policy=QueueRetryPolicy(max_retries=2, retry_intervals=(5, 30)),
    )

    fetched_job = Queue(name="telegram", connection=connection).fetch_job(
        enqueued.job_id
    )
    assert fetched_job is not None
    assert fetched_job.retries_left == 2
    assert fetched_job.timeout == 180
    assert client.is_available() is True


def test_rq_queue_client_detects_scheduler_liveness() -> None:
    connection = fakeredis.FakeRedis()
    client = RQQueueClient(connection=connection)

    assert client.is_scheduler_available(queue_name="telegram") is False
    connection.set(
        RQScheduler.get_locking_key("telegram"),
        "123",
        ex=10,
    )
    assert client.is_scheduler_available(queue_name="telegram") is True


def test_rq_worker_retries_transient_job_after_worker_restart() -> None:
    global _retry_attempts
    _retry_attempts = 0
    connection = fakeredis.FakeRedis()
    client = RQQueueClient(connection=connection)
    enqueued = client.enqueue(
        queue_name="telegram",
        function=retry_once_task,
        retry_policy=QueueRetryPolicy(max_retries=1, retry_intervals=(0,)),
    )

    first_worker = SimpleWorker(
        [Queue(name="telegram", connection=connection)],
        connection=connection,
    )
    first_worker.work(burst=True)
    second_worker = SimpleWorker(
        [Queue(name="telegram", connection=connection)],
        connection=connection,
    )
    second_worker.work(burst=True)

    fetched_job = Queue(name="telegram", connection=connection).fetch_job(
        enqueued.job_id
    )
    assert _retry_attempts == 2
    assert fetched_job is not None
    assert fetched_job.is_finished


def test_rq_scheduler_promotes_delayed_job_before_worker_consumes_it() -> None:
    connection = fakeredis.FakeRedis()
    client = RQQueueClient(connection=connection)
    enqueued = client.enqueue_in(
        queue_name="telegram",
        function=sample_task,
        seconds=0,
        args=("hello",),
        timeout_seconds=77,
    )

    queue = Queue(name="telegram", connection=connection)
    assert queue.count == 0
    assert ScheduledJobRegistry(queue=queue).get_job_count(cleanup=False) == 1

    SimpleWorker([queue], connection=connection).work(
        burst=True,
        with_scheduler=True,
    )

    fetched_job = queue.fetch_job(enqueued.job_id)
    assert fetched_job is not None
    assert fetched_job.is_finished
    assert fetched_job.timeout == 77
    assert fetched_job.return_value() == "HELLO"
    assert ScheduledJobRegistry(queue=queue).get_job_count(cleanup=False) == 0


def test_delayed_rq_retry_is_promoted_by_scheduler() -> None:
    global _retry_attempts
    _retry_attempts = 0
    connection = fakeredis.FakeRedis()
    client = RQQueueClient(connection=connection)
    enqueued = client.enqueue(
        queue_name="telegram",
        function=retry_once_task,
        retry_policy=QueueRetryPolicy(max_retries=1, retry_intervals=(1,)),
    )
    queue = Queue(name="telegram", connection=connection)

    SimpleWorker([queue], connection=connection).work(burst=True)
    assert ScheduledJobRegistry(queue=queue).get_job_count(cleanup=False) == 1

    import time

    time.sleep(1.1)
    SimpleWorker([queue], connection=connection).work(
        burst=True,
        with_scheduler=True,
    )

    fetched_job = queue.fetch_job(enqueued.job_id)
    assert _retry_attempts == 2
    assert fetched_job is not None
    assert fetched_job.is_finished
