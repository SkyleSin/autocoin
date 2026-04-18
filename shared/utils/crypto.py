"""API 키 암호화/복호화 — Fernet (AES-128-CBC + HMAC)."""

from __future__ import annotations

from cryptography.fernet import Fernet


def encrypt(plaintext: str, key: str) -> str:
    """평문을 암호화하여 base64 문자열 반환."""
    f = Fernet(key.encode() if isinstance(key, str) else key)
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str, key: str) -> str:
    """암호문을 복호화하여 평문 반환."""
    f = Fernet(key.encode() if isinstance(key, str) else key)
    return f.decrypt(ciphertext.encode()).decode()


def generate_key() -> str:
    """새 Fernet 키 생성."""
    return Fernet.generate_key().decode()
