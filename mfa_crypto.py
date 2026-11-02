import base64
import hashlib

from cryptography.fernet import Fernet

from config import Config


def encrypt_mfa_secret(secret: str) -> str:
    return _cipher().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_mfa_secret(encrypted_secret: str) -> str:
    return _cipher().decrypt(encrypted_secret.encode("ascii")).decode("utf-8")


def _cipher() -> Fernet:
    derived_key = hashlib.sha256(
        Config.SECRET_KEY.encode("utf-8") + b":greenfield-mfa-secret:v1"
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived_key))
