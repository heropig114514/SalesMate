"""Responsibility: Let background workers finish the current work unit after a deployment-stop signal.
Implementation: A temporary SIGTERM handler only sets a flag; callers check it before claiming new work and restore the original handler on exit.
Relationships: crm_worker waits for local or Celery work in the explicit executor; sales_worker waits for the current external action to persist.
Directory:
- graceful_shutdown: Provides a context carrying shutdown-request state.
- graceful_shutdown.request_shutdown: Records a SIGTERM shutdown request.
Variable index:
- None
"""
from contextlib import contextmanager
import signal


# Function: Convert SIGTERM into a stop request handled at a business boundary.
# Inputs: No arguments; reads the main thread's original SIGTERM handler.
# Outputs: State dictionary containing the requested boolean.
# Logic: The handler only sets state, neither raises an interrupt nor acquires a lock; finally restores the original handler.
# Constraints: Used only by management-command main threads; does not take over SIGINT, retry automatically, or abort the current external call.
@contextmanager
def graceful_shutdown():
    state = {"requested": False}

# Function: Record a deployment shutdown request.
# Inputs: `signum` is SIGTERM; `frame` is the interrupted Python frame.
# Outputs: None; updates closure state.
# Logic: Only assigns state, avoiding signal-handler reentry into locks or the logging system.
# Constraints: The caller must still finish the current unit and exit its loop deliberately.
    def request_shutdown(signum, frame):
        state["requested"] = True

    previous = signal.signal(signal.SIGTERM, request_shutdown)
    try:
        yield state
    finally:
        signal.signal(signal.SIGTERM, previous)
