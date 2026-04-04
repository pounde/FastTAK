"""Read configuration from environment variables (passed from .env via compose)."""

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Service versions — for update comparison
    tak_version: str = ""
    authentik_version: str = ""
    mediamtx_version: str = ""
    nodered_version: str = ""
    tak_portal_version: str = ""

    # Deployment
    server_address: str = "localhost"
    deploy_mode: str = "direct"

    # Subdomains (subdomain mode)
    takserver_subdomain: str = "takserver"
    mediamtx_subdomain: str = "stream"
    authentik_subdomain: str = "auth"
    takportal_subdomain: str = "portal"
    nodered_subdomain: str = "nodered"
    monitor_subdomain: str = "monitor"

    # Ports (direct mode)
    authentik_port: int = 9443
    nodered_port: int = 1880
    monitor_port: int = 8180
    takserver_admin_port: int = 8446
    mediamtx_port: int = 8888

    # Alert email (SMTP)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    alert_email: str = ""  # Recipient for health alerts

    # Alert SMS (optional)
    sms_provider: str = ""  # "twilio" or "brevo"
    sms_api_key: str = ""
    sms_from: str = ""
    sms_to: str = ""  # Comma-separated phone numbers

    # Database
    tak_db_password: str = ""
    tak_db_url: str = ""  # Override: postgresql://user:pass@host:port/db

    # Monitor settings
    update_check_interval: int = 21600  # 6 hours

    # Authentik
    authentik_url: str = "http://authentik-server:9000"
    authentik_api_token: str = ""

    # TAK Server API (mTLS)
    tak_server_url: str = "https://tak-server:8443"
    tak_api_cert_path: str = ""
    tak_api_cert_password: str = "atakatak"

    # User management
    users_hidden_prefixes: str = "ak-,adm_,svc_,ma-"
    user_expiry_check_interval: int = 60  # seconds
    enrollment_ttl_minutes: int = 15
    tak_enrollment_port: int = 8446

    model_config = ConfigDict(extra="ignore")


settings = Settings()
