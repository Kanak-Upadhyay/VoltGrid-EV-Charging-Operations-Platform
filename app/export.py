import csv
import io


COLUMNS = ["invoice_id", "session_id", "site", "city", "energy_kwh", "total_inr", "status", "issued_at"]


def revenue_csv(rows: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def store_export(name: str, payload: bytes) -> str:
    """Save a CSV locally, and to Azure Blob Storage when configured."""
    from pathlib import Path

    from app.config import get_settings

    settings = get_settings()
    folder = Path("exports")
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_bytes(payload)
    location = str(path)
    if not settings.azure_storage_connection_string:
        return location
    from azure.storage.blob import BlobServiceClient

    service = BlobServiceClient.from_connection_string(settings.azure_storage_connection_string)
    container = service.get_container_client(settings.azure_blob_container)
    if not container.exists():
        container.create_container()
    blob = container.get_blob_client(name)
    blob.upload_blob(payload, overwrite=True)
    return blob.url
