"""
Secure SUPER_ADMIN ensure / password init for Shafsky Aviation.

Uses AuthService.bcrypt hashing only. Never authenticates via plaintext env
bypass at login time. Intended for one-time ops against Neon/production DB.

Usage:
  # Create missing SUPER_ADMIN (or reset hash if --reset-password):
  set ADMIN_INIT_PASSWORD=...   # preferred over --password (less shell history)
  python -m app.scripts.ensure_super_admin --email admin@shafskyaviation.com --create

  # Reset password for an existing account only:
  python -m app.scripts.ensure_super_admin --email admin@shafskyaviation.com --reset-password

Password sources (first wins): --password CLI flag, then ADMIN_INIT_PASSWORD env.
The password is never printed.
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import select, func, or_

from app.database import SessionLocal
from app.models.schema import UserAuth, Role
from app.services.auth_service import AuthService


def _resolve_password(cli_password: str | None) -> str | None:
    if cli_password and cli_password.strip():
        return cli_password
    env = (os.getenv("ADMIN_INIT_PASSWORD") or "").strip()
    return env or None


def ensure_super_admin(
    email: str,
    password: str,
    *,
    create: bool,
    reset_password: bool,
) -> int:
    email_clean = email.strip().lower()
    if not email_clean or "@" not in email_clean:
        print("[-] Error: valid --email required.", file=sys.stderr)
        return 1
    if not password or len(password) < 8:
        print("[-] Error: password must be at least 8 characters (via --password or ADMIN_INIT_PASSWORD).", file=sys.stderr)
        return 1
    if not create and not reset_password:
        print("[-] Error: specify --create and/or --reset-password.", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        user = db.scalar(
            select(UserAuth).where(
                or_(
                    func.lower(UserAuth.email) == email_clean,
                    UserAuth.email == email.strip(),
                )
            )
        )

        if user is None:
            if not create:
                print(f"[-] User '{email_clean}' not found. Pass --create to seed SUPER_ADMIN.", file=sys.stderr)
                return 1
            hashed = AuthService.hash_password(password)
            if not AuthService.verify_password(password, hashed):
                print("[-] Internal error: hash verification failed after hash.", file=sys.stderr)
                return 1
            user = UserAuth(
                email=email_clean,
                password_hash=hashed,
                role=Role.SUPER_ADMIN,
                is_verified=True,
                is_active=True,
            )
            db.add(user)
            db.commit()
            print(f"[+] Created SUPER_ADMIN '{email_clean}' with bcrypt password hash.")
            return 0

        # Existing user — never change role downward; only promote to SUPER_ADMIN on --create
        if create and user.role != Role.SUPER_ADMIN:
            user.role = Role.SUPER_ADMIN
            user.is_verified = True
            user.is_active = True

        if reset_password or (create and (
            not user.password_hash
            or user.password_hash.startswith("mock")
            or not str(user.password_hash).startswith("$2")
        )):
            hashed = AuthService.hash_password(password)
            if not AuthService.verify_password(password, hashed):
                print("[-] Internal error: hash verification failed after hash.", file=sys.stderr)
                return 1
            user.password_hash = hashed
            user.is_active = True
            user.is_verified = True
            db.commit()
            role_name = user.role.value if hasattr(user.role, "value") else str(user.role)
            print(f"[+] Updated bcrypt password hash for '{email_clean}' ({role_name}).")
            return 0

        role_name = user.role.value if hasattr(user.role, "value") else str(user.role)
        print(
            f"[+] User '{email_clean}' already exists ({role_name}) with a bcrypt hash. "
            "No password change (pass --reset-password to rotate)."
        )
        return 0
    except Exception as exc:
        db.rollback()
        print(f"[-] Database error: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ensure SUPER_ADMIN exists with a bcrypt password hash (AuthService)."
    )
    parser.add_argument("--email", required=True, help="Admin email (stored lowercased on create)")
    parser.add_argument(
        "--password",
        default=None,
        help="New password (prefer ADMIN_INIT_PASSWORD env instead)",
    )
    parser.add_argument(
        "--create",
        action="store_true",
        help="Create SUPER_ADMIN if missing; promote role if present",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Replace password_hash with a new bcrypt hash for an existing user",
    )
    args = parser.parse_args()
    password = _resolve_password(args.password)
    raise SystemExit(
        ensure_super_admin(
            args.email,
            password or "",
            create=args.create,
            reset_password=args.reset_password,
        )
    )


if __name__ == "__main__":
    main()
