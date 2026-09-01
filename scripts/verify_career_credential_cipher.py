"""离线验证求职助手 Fernet 凭据加密，不连接数据库或外部模型服务。"""

from __future__ import annotations

import sys
from tempfile import TemporaryDirectory
from pathlib import Path

from cryptography.fernet import Fernet


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.persistence.credential_cipher import (
    FERNET_V1_SCHEME,
    LEGACY_PLAINTEXT_SCHEME,
    LEGACY_UNKNOWN_SCHEME,
    CredentialCipher,
    CredentialCipherError,
    MASTER_KEY_ENV_NAME,
    ensure_credential_master_key,
)


def main() -> None:
    """验证加密、完整性校验、旧明文边界和无主密钥拒绝写入行为。"""

    api_key = "verification-only-api-key"
    cipher = CredentialCipher.from_master_key(Fernet.generate_key())
    encrypted_api_key = cipher.encrypt(api_key)
    assert encrypted_api_key != api_key.encode("utf-8")
    assert cipher.decrypt(
        encryption_scheme=FERNET_V1_SCHEME,
        encrypted_api_key=encrypted_api_key,
        plaintext_api_key=None,
    ) == api_key

    try:
        cipher.decrypt(
            encryption_scheme=FERNET_V1_SCHEME,
            encrypted_api_key=b"not-a-valid-fernet-token",
            plaintext_api_key=None,
        )
    except CredentialCipherError as exc:
        assert "无法解密" in str(exc)
    else:
        raise AssertionError("篡改的 Fernet token 必须被拒绝")

    strict_legacy_cipher = CredentialCipher.from_environment(
        {"CAREER_ALLOW_LEGACY_PLAINTEXT_CREDENTIALS": "false"},
    )
    try:
        strict_legacy_cipher.decrypt(
            encryption_scheme=LEGACY_PLAINTEXT_SCHEME,
            encrypted_api_key=None,
            plaintext_api_key=api_key,
        )
    except CredentialCipherError as exc:
        assert "旧版明文" in str(exc)
    else:
        raise AssertionError("默认配置不得读取旧明文凭据")

    compatible_legacy_cipher = CredentialCipher.from_environment(
        {"CAREER_ALLOW_LEGACY_PLAINTEXT_CREDENTIALS": "true"},
    )
    assert compatible_legacy_cipher.decrypt(
        encryption_scheme=LEGACY_PLAINTEXT_SCHEME,
        encrypted_api_key=None,
        plaintext_api_key=api_key,
    ) == api_key

    try:
        compatible_legacy_cipher.decrypt(
            encryption_scheme=LEGACY_UNKNOWN_SCHEME,
            encrypted_api_key=b"legacy-ciphertext",
            plaintext_api_key=None,
        )
    except CredentialCipherError as exc:
        assert "无法识别" in str(exc)
    else:
        raise AssertionError("未知旧密文不得被当作 API Key 使用")

    try:
        CredentialCipher.from_environment({}).encrypt(api_key)
    except CredentialCipherError as exc:
        assert "CAREER_CREDENTIAL_MASTER_KEY" in str(exc)
    else:
        raise AssertionError("未配置主密钥时不得写入新 API Key")

    invalid_configuration_cipher = CredentialCipher.from_environment(
        {"CAREER_CREDENTIAL_MASTER_KEY": "not-a-fernet-key"},
    )
    assert not invalid_configuration_cipher.can_encrypt
    try:
        invalid_configuration_cipher.encrypt(api_key)
    except CredentialCipherError as exc:
        assert "格式无效" in str(exc)
    else:
        raise AssertionError("格式错误的主密钥不得降级为明文写入")

    with TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        first_environment: dict[str, str] = {}
        managed_key_path = ensure_credential_master_key(
            temporary_root,
            environment=first_environment,
        )
        assert managed_key_path == temporary_root / "data" / "career_credential_master.key"
        assert managed_key_path.is_file()
        assert CredentialCipher.from_environment(first_environment).can_encrypt

        second_environment: dict[str, str] = {}
        second_key_path = ensure_credential_master_key(
            temporary_root,
            environment=second_environment,
        )
        assert second_key_path == managed_key_path
        assert second_environment[MASTER_KEY_ENV_NAME] == first_environment[MASTER_KEY_ENV_NAME]

        explicit_environment = {MASTER_KEY_ENV_NAME: Fernet.generate_key().decode("ascii")}
        assert ensure_credential_master_key(
            temporary_root / "explicit",
            environment=explicit_environment,
        ) is None
        assert not (temporary_root / "explicit" / "data").exists()

    print("career_credential_cipher_offline_ok")


if __name__ == "__main__":
    main()
