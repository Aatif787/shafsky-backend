import os
import json
import hashlib
import base64
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List
from cryptography.fernet import Fernet
from app.config import settings
from app.database import Base

logger = logging.getLogger("shafsky.disaster_recovery.backup")


class BackupEngine:
    """DR-drill backup engine.

    Produces encrypted schema-metadata artifacts on local disk so restore/verify
    drills can be exercised end to end. It does NOT dump table data and does NOT
    upload to S3 — production recovery depends on RDS automated snapshots/PITR.
    """

    BACKUP_DIR = os.path.join(os.getcwd(), "backups")

    @classmethod
    def ensure_backup_directory(cls):
        if not os.path.exists(cls.BACKUP_DIR):
            os.makedirs(cls.BACKUP_DIR, exist_ok=True)

    @classmethod
    def _get_fernet_key(cls, secret_key: str) -> bytes:
        """Derive a valid Fernet 32-byte urlsafe base64 key from any secret string."""
        digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    @classmethod
    def encrypt_data(cls, raw_data: str, secret_key: str) -> str:
        """Encrypts data using authenticated Fernet (AES-128-CBC + HMAC-SHA256)."""
        key = cls._get_fernet_key(secret_key)
        f = Fernet(key)
        return f.encrypt(raw_data.encode("utf-8")).decode("utf-8")

    @classmethod
    def decrypt_data(cls, encrypted_data: str, secret_key: str) -> str:
        """Decrypts authenticated Fernet payload."""
        key = cls._get_fernet_key(secret_key)
        f = Fernet(key)
        return f.decrypt(encrypted_data.encode("utf-8")).decode("utf-8")

    @classmethod
    def generate_database_backup(cls) -> Dict[str, Any]:
        """
        Creates an encrypted, authenticated schema-metadata drill artifact with
        real SHA-256 integrity verification. Not a database dump, not an S3 backup.
        """
        cls.ensure_backup_directory()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_id = f"bak_db_{timestamp}"
        filename = f"{backup_id}.enc"
        filepath = os.path.join(cls.BACKUP_DIR, filename)

        # DR drill artifact only — captures registered schema metadata, NOT table
        # rows. This is not a database dump and is not shipped to S3; production
        # recovery relies on RDS automated snapshots / PITR.
        registered_tables = sorted(list(Base.metadata.tables.keys()))
        table_count = len(registered_tables)

        dump_payload = json.dumps({
            "backup_id": backup_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "artifact_type": "METADATA_DRILL_ONLY",
            "warning": "Not a real database backup. Use RDS snapshots/PITR in production.",
            "database_target": "PostgreSQL",
            "schema_version": "2.0.0",
            "table_count": table_count,
            "registered_tables": registered_tables,
            "status": "DRILL_COMPLETED",
        }, indent=2)

        secret = getattr(settings, "JWT_REFRESH_SECRET", None) or getattr(settings, "JWT_SECRET", "shafsky-backup-encryption-key")
        encrypted_content = cls.encrypt_data(dump_payload, secret)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(encrypted_content)

        checksum = hashlib.sha256(encrypted_content.encode("utf-8")).hexdigest()

        meta_filename = f"{backup_id}.meta.json"
        meta_filepath = os.path.join(cls.BACKUP_DIR, meta_filename)

        # Check cloud sync configuration
        # Nothing is uploaded here; the artifact only ever exists on local disk.
        s3_bucket = getattr(settings, "AWS_S3_BUCKET", None)
        s3_status = "NOT_SYNCED_LOCAL_ONLY"
        if s3_bucket:
            s3_status = "NOT_SYNCED_S3_DISPATCH_NOT_IMPLEMENTED"

        meta_data = {
            "backupId": backup_id,
            "filename": filename,
            "checksumSha256": checksum,
            "sizeBytes": os.path.getsize(filepath),
            "encryption": "FERNET_AES128_HMAC_SHA256",
            "tableCount": table_count,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "s3SyncStatus": s3_status,
            "isProductionBackup": False,
            "warning": "Schema-metadata drill artifact only. Rely on RDS automated backups/PITR for production.",
        }
        with open(meta_filepath, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, indent=2)

        logger.info(f"DR drill artifact {backup_id} created with schema metadata for {table_count} tables.")
        return meta_data

    @classmethod
    def list_backups(cls) -> List[Dict[str, Any]]:
        cls.ensure_backup_directory()
        backups = []
        for file in os.listdir(cls.BACKUP_DIR):
            if file.endswith(".meta.json"):
                try:
                    with open(os.path.join(cls.BACKUP_DIR, file), "r", encoding="utf-8") as f:
                        backups.append(json.load(f))
                except Exception as err:
                    logger.warning(f"Failed to read backup metadata file {file}: {err}")
        backups.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return backups
