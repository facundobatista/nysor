"""Small utilities to help other code."""

import asyncio
import logging

logger = logging.getLogger(__name__)


# collection so futurized tasks are always referenced while alive, to avoid
# premature garbage collection
_futurized_background_tasks = set()


def _future_cleanup(task):
    """Cleanup for futurized tasks."""
    # remove it from the global holding set
    _futurized_background_tasks.discard(task)

    # get the result only to properly log an exception if happened
    try:
        task.result()
    except Exception:
        logger.exception("Futurized call crashed")


def call_async(async_function, *args, **kwargs):
    """Call an async function / couroutine in the future.

    Used to call coroutines from blocking code. As the execution is in the future, there is no
    way to get the result of that call. This function just returns None.
    """
    # get the coroutine, and a task from it
    coro = async_function(*args, **kwargs)
    task = asyncio.create_task(coro)

    # include it in the global set so it's never un-referenced until completion
    _futurized_background_tasks.add(task)

    # plan the cleanup
    task.add_done_callback(_future_cleanup)
