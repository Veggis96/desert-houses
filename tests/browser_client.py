from flask.testing import FlaskClient


class BrowserClient(FlaskClient):
    """Submit gameplay forms with real session CSRF tokens; csrf=False tests rejection."""
    def post(self, *args, csrf=True, **kwargs):
        if csrf:
            with self.session_transaction() as state:
                token = state.get("auth_csrf_token")
                logged_in = "user_id" in state
            if not token:
                self.get("/account" if logged_in else "/login", follow_redirects=True)
                with self.session_transaction() as state:
                    token = state.get("auth_csrf_token", "")
            data = dict(kwargs.get("data") or {})
            data.setdefault("csrf_token", token)
            kwargs["data"] = data
        return super().post(*args, **kwargs)
