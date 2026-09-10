"""
Shafsky Aviation — Password Reset Utility Script.

Allows resetting the password of any user account (SUPER_ADMIN, ADMIN, CUSTOMER, etc.)
with bcrypt hashing and verification.

Usage:
    python -m app.scripts.reset_password --email admin@shafskyaviation.com --password "NewPassword123!"
    python -m app.scripts.reset_password --email ops@shafskyaviation.com --password "NewPassword123!"
"""

import argparse
import sys
from sqlalchemy import select, func, or_
from app.database import SessionLocal
from app.models.schema import UserAuth
from app.services.auth_service import AuthService


def reset_password(email: str, new_password: str) -> bool:
    raw_email = email.strip()
    email_clean = raw_email.lower()
    if not new_password or len(new_password) < 8:
        print("[-] Error: Password must be at least 8 characters long.", file=sys.stderr)
        return False

    db = SessionLocal()
    try:
        user = db.scalar(
            select(UserAuth).where(
                or_(
                    func.lower(UserAuth.email) == email_clean,
                    UserAuth.email == raw_email,
                    UserAuth.email == email_clean
                )
            )
        )
        if not user:
            print(f"[-] Error: User with email '{email_clean}' not found in database.", file=sys.stderr)
            return False

        # Hash new password with bcrypt
        hashed = AuthService.hash_password(new_password)
        user.password_hash = hashed
        db.commit()

        role_name = user.role.value if hasattr(user.role, "value") else str(user.role)
        print(f"[+] Success: Password for {email_clean} ({role_name}) updated successfully!")
        return True
    except Exception as e:
        db.rollback()
        print(f"[-] Database Error: {e}", file=sys.stderr)
        return False
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Reset user password in Shafsky Aviation database.")
    parser.add_argument("--email", required=True, help="User email address (e.g. admin@shafskyaviation.com)")
    parser.add_argument("--password", required=True, help="New plain-text password")
    args = parser.parse_args()

    success = reset_password(args.email, args.password)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
