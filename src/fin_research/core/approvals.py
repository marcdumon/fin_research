"""Pause a run until a human decides, without holding that decision in memory.

The decision arrives from a different process -- someone clicking in the dashboard, which writes to the
approvals table over HTTP. There is nothing in this process to await, so the run polls for the answer. That
is the same reason the event stream polls, and it has a property worth having: the pending request is a row,
so a run interrupted while waiting can be resumed and a restart does not strand it.

Gates are off unless asked for. A run started from the command line with nobody watching would otherwise
block until it timed out, turning an unattended job into a silent stall.
"""

import asyncio
import logging

from fin_research.core.state import State

log = logging.getLogger(__name__)

POLL_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 900.0

APPROVED = 'approved'
REJECTED = 'rejected'
EXPIRED = 'expired'


class Rejected(RuntimeError):
    """Raised when a human declined a step, or did not answer in time."""


def decision(state: State, approval_id: str) -> str | None:
    """The answer recorded for a request, or None while it is still pending."""
    rows = state.query('SELECT status FROM approvals WHERE approval_id = ?', (approval_id,))
    if not rows:
        return None
    status = rows[0]['status']
    return None if status == 'pending' else status


async def wait_for_decision(state: State, approval_id: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                            poll: float = POLL_SECONDS) -> str:
    """Block until the request is answered, or until `timeout` elapses.

    A timeout is recorded as a decision rather than left pending, so the history says what happened instead
    of showing a request that appears to still be waiting on someone.
    """
    waited = 0.0
    while waited < timeout:
        answer = decision(state, approval_id)
        if answer is not None:
            return answer
        await asyncio.sleep(poll)
        waited += poll

    state.decide_approval(approval_id, EXPIRED, f'no answer within {timeout:.0f}s')
    return EXPIRED


async def gate(state: State, run_id: str, *, subject: str, prompt: str, task_id: str | None = None,
               timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
    """Ask for approval and wait, raising `Rejected` unless the answer is yes."""
    approval_id = state.request_approval(run_id, subject, prompt, task_id)
    log.info(f'waiting for approval: {prompt}')

    answer = await wait_for_decision(state, approval_id, timeout=timeout)
    if answer != APPROVED:
        raise Rejected(f'{prompt} -- {answer}')

    log.info(f'approved: {prompt}')
