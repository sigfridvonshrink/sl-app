from flask import Blueprint
from flask import request
from flask_login import current_user
from app.certificate_auth import get_user_from_cert_subject
from app.auth.views.login_utils import after_login
from app.events.auth_event import LoginEvent
from app.utils import sanitize_next_url

auth_bp = Blueprint(
    name="auth", import_name=__name__, url_prefix="/auth", template_folder="templates"
)

# … all your existing imports and auth_bp declaration …


@auth_bp.before_request
def cert_auto_login():
    # only intercept the /login endpoint
    if request.endpoint != "auth.login":
        return

    # if already logged in via session cookie, do nothing
    if current_user.is_authenticated:
        return

    # try to extract client‐cert subject header
    cert_subject = request.headers.get("X-SSL-Client-Subject")
    if not cert_subject:
        return

    # lookup user via certificate helper
    user = get_user_from_cert_subject(cert_subject)
    if not user:
        return

    # on success fire event + hand off to after_login.
    # login_from_proton=True reuses the existing 2FA-bypass path: a valid client
    # certificate is itself strong auth, so cert logins intentionally skip OTP/FIDO.
    LoginEvent(LoginEvent.ActionType.success).send()
    next_url = sanitize_next_url(request.args.get("next"))
    return after_login(user, next_url, login_from_proton=True)
