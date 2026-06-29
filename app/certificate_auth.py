import re
from typing import Optional
from app.models import User
from app.log import LOG


def extract_email_from_cert_subject(subject: str) -> Optional[str]:
    """
    Extract an email from a certificate subject string.
    Tries in order:
      1) emailAddress=foo@bar
      2) CN=foo@bar
    """
    # 1) Look for an explicit emailAddress field
    email_match = re.search(r"emailAddress=([^,\/]+)", subject)
    if email_match:
        return email_match.group(1).lower()

    # 2) Fallback to CN=... only if it looks like an email
    cn_match = re.search(r"CN=([^,\/]+)", subject)
    if cn_match:
        potential = cn_match.group(1)
        if "@" in potential:
            return potential.lower()

    return None


def get_user_from_cert_subject(subject: str) -> Optional[User]:
    if not subject:
        return None

    email = extract_email_from_cert_subject(subject)
    if not email:
        LOG.warning(f"Could not extract email from certificate subject: {subject}")
        return None

    user = User.get_by(email=email)
    if not user:
        LOG.warning(f"No user found with email: {email} from certificate")
    return user
