import logging
from datetime import datetime, timezone

from app.crypto import decrypt_config
from app.db import SessionLocal
from app.models import ExportJob, Integration, JobStatus
from app.services.exporters.common import approved_versions
from app.services.exporters.csv_export import CSV_FORMATS, build_csv
from app.services.exporters.platforms import PlatformError, get_client
from app.services.storage import save_file

log = logging.getLogger(__name__)

PLATFORM_FORMATS = ("shopify", "woocommerce")
EXPORT_FORMATS = CSV_FORMATS + PLATFORM_FORMATS


def run_export(export_id: int) -> None:
    db = SessionLocal()
    export: ExportJob | None = None
    try:
        export = db.get(ExportJob, export_id)
        if export is None:
            return
        export.status = JobStatus.RUNNING
        db.commit()

        rows = approved_versions(db, export.catalog_id, export.language)
        export.total = len(rows)

        if export.format in CSV_FORMATS:
            key = f"org_{export.org_id}/exports/export_{export.id}_{export.format}_{export.language}.csv"
            export.file_key = save_file(key, build_csv(export.format, rows))
            export.succeeded = len(rows)
        else:
            integration = db.get(Integration, export.integration_id) if export.integration_id else None
            if integration is None or integration.org_id != export.org_id:
                raise PlatformError("Integration not found")
            client = get_client(integration.platform, decrypt_config(integration.config))
            for product, version in rows:
                try:
                    client.push(product, version)
                    export.succeeded += 1
                except Exception as exc:
                    export.failed += 1
                    message = str(exc) if isinstance(exc, PlatformError) else f"Request failed: {exc}"
                    export.errors = [*(export.errors or []), {"sku": product.sku, "error": message}][-200:]
                db.commit()

        export.status = JobStatus.COMPLETED if not export.failed or export.succeeded else JobStatus.FAILED
    except Exception as exc:
        log.exception("Export %s failed", export_id)
        db.rollback()
        export = db.get(ExportJob, export_id)
        if export is not None:
            export.status = JobStatus.FAILED
            export.errors = [*(export.errors or []), {"error": str(exc) if isinstance(exc, PlatformError) else "Export failed"}]
    finally:
        if export is not None:
            export.finished_at = datetime.now(timezone.utc)
            db.commit()
            from app.services import webhooks

            webhooks.safe_emit(db, export.org_id, "export.completed", {
                "export_id": export.id, "catalog_id": export.catalog_id, "format": export.format,
                "language": export.language, "status": export.status, "succeeded": export.succeeded,
                "failed": export.failed})
        db.close()
