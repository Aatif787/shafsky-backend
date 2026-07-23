from fastapi import APIRouter, HTTPException, Depends, Header
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from app.schemas.auth import ApiResponse
from app.security.permissions import require_roles
from app.disaster_recovery.backup_engine import BackupEngine
from app.disaster_recovery.restore_engine import RestoreEngine
from app.disaster_recovery.runbook import DisasterSimulator, FailoverEngine

router = APIRouter(prefix="/api/admin/dr", tags=["Disaster Recovery & Business Continuity"])

@router.get("/status", response_model=ApiResponse)
async def get_dr_status(
    auth_data: dict = Depends(require_roles(["SUPER_ADMIN", "ADMIN"]))
):
    backups = BackupEngine.list_backups()
    latest_backup = backups[0] if backups else None

    status_data = {
        "drStatus": "HEALTHY_READY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "slaTargets": {
            "bookingEngineRPO": "<= 5 Minutes",
            "bookingEngineRTO": "<= 15 Minutes",
            "criticalApisRPO": "<= 15 Minutes",
            "criticalApisRTO": "<= 30 Minutes"
        },
        "pitrStatus": "NEON_WAL_ARCHIVING_ACTIVE",
        "latestBackup": latest_backup,
        "totalBackupsCount": len(backups),
        "runbooks": DisasterSimulator.get_runbook_procedures()
    }
    return ApiResponse(success=True, data=status_data)

@router.post("/backup", response_model=ApiResponse)
async def trigger_backup(
    auth_data: dict = Depends(require_roles(["SUPER_ADMIN", "ADMIN"]))
):
    meta_data = BackupEngine.generate_database_backup()
    return ApiResponse(
        success=True,
        data={
            "message": "AES-256 encrypted database backup generated successfully.",
            "backupMetadata": meta_data
        }
    )

@router.post("/restore-verify", response_model=ApiResponse)
async def verify_restore(
    backup_id: Optional[str] = None,
    auth_data: dict = Depends(require_roles(["SUPER_ADMIN", "ADMIN"]))
):
    if not backup_id:
        backups = BackupEngine.list_backups()
        if not backups:
            raise HTTPException(status_code=404, detail="No backup artifacts found to verify.")
        backup_id = backups[0]["backupId"]

    result = RestoreEngine.verify_backup_integrity(backup_id)
    if not result.get("verified"):
        raise HTTPException(status_code=400, detail=result.get("error", "Restore verification failed."))

    return ApiResponse(success=True, data=result)

@router.post("/simulate-incident", response_model=ApiResponse)
async def simulate_dr_incident(
    scenario: str,
    auth_data: dict = Depends(require_roles(["SUPER_ADMIN"]))
):
    res = DisasterSimulator.simulate_incident(scenario)
    return ApiResponse(success=True, data=res)
