"""One-shot reconciler. Azure Container Apps Jobs or App Service cron can run:

    python -m app.worker
"""

from app.db import init_db, new_session
from app.services import reconcile_stale_chargers


def run_once() -> int:
    init_db()
    db = new_session()
    try:
        return reconcile_stale_chargers(db)
    finally:
        db.close()


if __name__ == "__main__":
    print(f"marked_offline={run_once()}")
