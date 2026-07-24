import pytest
from cryptography.fernet import Fernet
from rufo_control_plane.secrets import SecretCipher


def test_encrypt_decrypt_round_trip():
    cipher = SecretCipher(Fernet.generate_key().decode())
    ciphertext = cipher.encrypt("sk-real-secret-value")
    assert ciphertext != "sk-real-secret-value"
    assert cipher.decrypt(ciphertext) == "sk-real-secret-value"


def test_wrong_key_fails_to_decrypt():
    cipher_a = SecretCipher(Fernet.generate_key().decode())
    cipher_b = SecretCipher(Fernet.generate_key().decode())
    ciphertext = cipher_a.encrypt("sk-real-secret-value")
    with pytest.raises(ValueError):
        cipher_b.decrypt(ciphertext)


def test_missing_master_key_raises():
    with pytest.raises(ValueError):
        SecretCipher("")
