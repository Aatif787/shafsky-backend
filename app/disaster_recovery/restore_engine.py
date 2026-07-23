import os
import json
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any
from app.disaster_recovery.backup_engine import BackupEngine
from app.config import settings

class RestoreEngine:
    @classmethod
    def verify_backup_integrity(cls, backup_id: str) -> Dict[str, Any]:
        meta_filepath = os.path.join(BackupEngine.BACKUP_DIR, f"{backup_id}.meta.json")
        enc_filepath = os.path.join(BackupEngine.BACKUP_DIR, f"{backup_id}.enc")

        if not os.path.exists(meta_filepath) or not os.path.exists(enc_filepath):
            return {"verified": False, "error": f"Backup ID '{backup_id}' not found."}

        with open(meta_filepath, "r", encoding="utf-8") as f:
            meta = json.load(f)

        with open(enc_filepath, "r", encoding="utf-8") as f:
            enc_content = f.read()

        calculated_checksum = hashlib.sha256(enc_content.encode("utf-8")).hexdigest()
        if calculated_checksum != meta.get("checksumSha256"):
            return {"verified": False, "error": "Checksum mismatch! Backup artifact corrupted or tampered."}

        secret = getattr(settings, "JWT_SECRET", "shafsky-backup-encryption-key")
        try:
            decrypted_str = BackupEngine.decrypt_data(enc_content, secret)
            payload = json.loads(decrypted_str)
        except Exception as e:
            return {"verified": False, "error": f"Decryption failed: {str(e)}"}

        return {
            "verified": True,
            "backupId": backup_id,
            "checksumVerified": True,
            "decryptionVerified": True,
            "schemaVersion": payload.get("schema_version"),
            "tableCount": payload.get("table_count"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
