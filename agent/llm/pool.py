"""Responsibility: Persist an ordered Bailian model pool and model-specific quota exclusions.
Implementation: SQLite commits exclusions atomically across local Worker processes; endpoint and credential hashes isolate accounts. No time-based reactivation occurs.
Relationships: bailian uses this pool only when explicitly configured; module CLI reads root .env for status and explicit restoration.
Directory:
- PoolError: Configuration or durable-state failure.
- ModelPool: Validated ordered candidates and shared state access.
- ModelPool.__init__: Validate model identifiers and absolute state path.
- ModelPool.from_env: Build the optional pool from environment.
- ModelPool._database: Open, initialize, commit, and close SQLite safely.
- ModelPool.disabled: Return persisted exclusions for this credential scope.
- ModelPool.disable: Atomically retain the first quota-exhaustion observation.
- ModelPool.restore: Explicitly re-enable one configured candidate.
- ModelPool.status: Project current, eligible, and unavailable model lists.
- main: Show state or explicitly restore a model from the command line.
Variable index:
- logger: Logs state changes without credentials, prompts, or provider response bodies.
- QUOTA_CODES: Verified provider codes that identify exhausted allocation rather than rate limiting.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3

logger = logging.getLogger("salesmate.llm.pool")
QUOTA_CODES = frozenset({"insufficient_quota", "AllocationQuota.FreeTierOnly"})


# Function: Represent invalid pool settings or unavailable persistence.
# Logic: Fail explicitly instead of silently using an untracked model.
# Constraints: Error text never includes credentials or raw database exceptions.
class PoolError(RuntimeError):
    pass


# Function: Manage ordered candidates and shared durable quota exclusions.
# Logic: Each operation uses a short SQLite transaction; no lock is held over network requests.
# Constraints: Already in-flight requests can overlap an exclusion, but future selections read shared state.
class ModelPool:
    # Function: Validate and retain pool configuration.
    # Inputs: Ordered `models`, absolute `state_path`, endpoint `base_url`, and private `api_key`.
    # Outputs: Instance with models, path, and a non-reversible credential-scope digest.
    # Logic: Reject empty/duplicate models and invalid paths before creating any database.
    # Constraints: Parent directory must already exist; no credentials are persisted.
    def __init__(self, models, state_path, base_url, api_key):
        if not models or any(not isinstance(m, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}", m) for m in models) or len(set(models)) != len(models):
            raise PoolError("BAILIAN_MODELS must contain unique nonempty model IDs in priority order.")
        path = Path(state_path)
        if not path.is_absolute() or not path.parent.is_dir():
            raise PoolError("BAILIAN_MODEL_STATE_DB must be an absolute SQLite path in an existing shared directory.")
        self.models = tuple(models)
        self.path = path
        self.scope = hashlib.sha256((base_url.rstrip("/") + "\0" + api_key).encode()).hexdigest()

    # Function: Resolve the explicitly configured pool.
    # Inputs: Validated `base_url`, private `api_key`, and BAILIAN_MODELS/BAILIAN_MODEL_STATE_DB environment.
    # Outputs: ModelPool or None for the existing single-model configuration.
    # Logic: A comma-separated ordered list requires durable state; no implicit in-memory substitute exists.
    # Constraints: Does not change the legacy model or thinking configuration.
    @classmethod
    def from_env(cls, base_url, api_key):
        configured = os.getenv("BAILIAN_MODELS", "").strip()
        if not configured:
            return None
        return cls([model.strip() for model in configured.split(",")], os.getenv("BAILIAN_MODEL_STATE_DB", "").strip(), base_url, api_key)

    # Function: Provide a short-lived database transaction.
    # Inputs: Instance state path; no explicit parameters.
    # Outputs: SQLite connection yielded to the caller, then committed and closed.
    # Logic: Initialize a uniquely keyed table, wait at most five seconds for database locks, and sanitize storage failures.
    # Constraints: Contains model IDs and error codes only; no network call may run inside this context.
    @contextmanager
    def _database(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS unavailable (scope TEXT NOT NULL, model TEXT NOT NULL, code TEXT NOT NULL, http_status INTEGER NOT NULL, disabled_at TEXT NOT NULL, PRIMARY KEY(scope, model))")
                yield connection
        except (sqlite3.Error, OSError) as error:
            logger.error("llm_pool_storage_failed error_type=%s action=check_shared_database_permissions", type(error).__name__)
            raise PoolError("Model pool state is unavailable; check the shared SQLite file and directory permissions.") from None
        finally:
            if connection is not None:
                connection.close()

    # Function: Read disabled models in the current provider credential scope.
    # Inputs: Instance scope; no explicit parameters.
    # Outputs: Dictionary keyed by model with safe code, HTTP status, and UTC timestamp.
    # Logic: Re-read shared durable state so Worker processes observe prior exclusions.
    # Constraints: A database failure is raised, never treated as an empty exclusion list.
    def disabled(self):
        with self._database() as database:
            rows = database.execute("SELECT model, code, http_status, disabled_at FROM unavailable WHERE scope = ?", (self.scope,)).fetchall()
        return {model: {"model": model, "code": code, "http_status": status, "disabled_at": at} for model, code, status, at in rows}

    # Function: Persist an explicitly observed quota exhaustion.
    # Inputs: Configured `model`, quota error `code`, and HTTP `status` of 403 or 429.
    # Outputs: None; first observation remains durable across repeated concurrent reports.
    # Logic: Validate the classification and atomically insert only if absent.
    # Constraints: Generic 429 rate limits, authentication errors, and transport failures cannot enter this list.
    def disable(self, model, code, status):
        if model not in self.models or code not in QUOTA_CODES or status not in (403, 429):
            raise PoolError("Only a configured model with an explicit quota-exhaustion response can be disabled.")
        with self._database() as database:
            database.execute("INSERT OR IGNORE INTO unavailable VALUES (?, ?, ?, ?, ?)", (self.scope, model, code, status, datetime.now(timezone.utc).isoformat()))
        logger.warning("llm_model_disabled model=%s http_status=%s code=%s reason=quota_exhausted", model, status, code)

    # Function: Explicitly restore a configured model after operator review.
    # Inputs: Model ID `model`.
    # Outputs: Boolean indicating whether an exclusion was removed.
    # Logic: Delete only this endpoint/credential/model entry, preserving other pools.
    # Constraints: Does not call the provider or claim that quota has actually recovered.
    def restore(self, model):
        if model not in self.models:
            raise PoolError("The model is not in BAILIAN_MODELS.")
        with self._database() as database:
            changed = database.execute("DELETE FROM unavailable WHERE scope = ? AND model = ?", (self.scope, model)).rowcount > 0
        logger.info("llm_model_restored model=%s changed=%s", model, changed)
        return changed

    # Function: Project model selection and exclusion state.
    # Inputs: Configured candidates and durable exclusions; no explicit parameters.
    # Outputs: Ordered candidates, eligible list, current selection, and unavailable metadata.
    # Logic: Select the first candidate without an exclusion; never expose the credential digest.
    # Constraints: Eligible means not quota-blocked, not a guarantee of current provider availability.
    def status(self):
        disabled = self.disabled()
        available = [model for model in self.models if model not in disabled]
        return {"candidates": list(self.models), "available": available, "current": available[0] if available else None,
                "unavailable": [disabled[model] for model in self.models if model in disabled]}


# Function: Inspect the pool or explicitly restore a model.
# Inputs: CLI status or restore MODEL; project-root .env and environment overrides.
# Outputs: Safe JSON state on stdout; configuration/storage failure exits nonzero.
# Logic: Load root configuration without overriding process values, then execute one explicit action.
# Constraints: Never changes provider billing, probes models, sends messages, or restores automatically.
def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    parser = argparse.ArgumentParser(description="Inspect model quota exclusions or restore one model explicitly.")
    parser.add_argument("command", choices=["status", "restore"])
    parser.add_argument("model", nargs="?")
    args = parser.parse_args()
    base_url, api_key = os.getenv("BAILIAN_BASE_URL", "").strip(), os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not base_url or not api_key:
        parser.error("Configure BAILIAN_BASE_URL and DASHSCOPE_API_KEY first.")
    try:
        pool = ModelPool.from_env(base_url, api_key)
        if pool is None:
            raise PoolError("BAILIAN_MODELS is not configured.")
        if args.command == "restore":
            if not args.model:
                parser.error("restore requires a model ID")
            pool.restore(args.model)
        elif args.model:
            parser.error("status takes no model ID")
        print(json.dumps(pool.status(), ensure_ascii=False, indent=2))
    except PoolError as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
