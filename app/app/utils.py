import os
import json
from typing import Dict, Tuple
from flask import current_app
from google.cloud import storage, kms_v1
from google.cloud.kms_v1 import enums
from google.cloud.kms_v1.types import CryptoKey


from google.api_core.exceptions import NotFound, FailedPrecondition

key_client = kms_v1.KeyManagementServiceClient()
storage_client = storage.Client()


def get_keyring_path():
    return key_client.key_ring_path(current_app.config["PROJECT_ID"], "global", "exchange_auth")


def get_key_path(user_id: int, exchange_name: str) -> str:
    return key_client.crypto_key_path_path(
        current_app.config["PROJECT_ID"],
        "global",
        "exchange_auth",
        f"{exchange_name}_{user_id}_key",
    )


def create_user_exchange_key(user_id: int, exchange_name: str) -> CryptoKey:
    current_app.logger.info("Creating new crypto key for user {} {} auth")
    keyring_path = get_keyring_path()

    crypto_key_id = f"{exchange_name}_{user_id}_key"
    purpose = enums.CryptoKey.CryptoKeyPurpose.ENCRYPT_DECRYPT
    crypto_key = {"purpose": purpose}
    key = key_client.create_crypto_key(keyring_path, crypto_key_id, crypto_key)
    current_app.logger.debug(
        f"Created crypto key {key.name} - version {key.primary.name}"
    )
    return key


def destroy_user_exchange_key(user_id: int, exchange_name: str) -> None:
    current_app.logger.info(f"Deleting crypto key for user {user_id} {exchange_name} auth")
    key_path = get_key_path(user_id, exchange_name)
    key = key_client.get_crypto_key(key_path)

    version_name = key.primary.name
    key_version = key_client.destroy_crypto_key_version(version_name)
    return key_version


def encrypt_user_auth(exchange_dict: Dict[str, str], user_id: int) -> bytes:
    exchange_name = exchange_dict["name"]
    current_app.logger.debug(f"Encrypting User {user_id} exchange auth for {exchange_name}")

    key_path = get_key_path(user_id, exchange_name)

    plaintext = json.dumps(exchange_dict).encode()

    try:
        key = key_client.get_crypto_key(key_path)
    except NotFound:
        key = create_user_exchange_key(user_id, exchange_name)

    current_app.logger.debug(f"Current kms key: {key.name}")

    key_version = key.primary

    if key_version.state == enums.CryptoKeyVersion.CryptoKeyVersionState.DESTROY_SCHEDULED:
        # key_client.create_crypto_key_version(key_path)

        current_app.logger.debug("Restoring DESTROY_SCHEDULED key")
        key_version = key_client.restore_crypto_key_version(key_version.name)
        current_app.logger.debug("Re-enabling key")
        key_version = key_client.update_crypto_key_version(key_version, {"state": "ENABLED"})

    if key_version.state == enums.CryptoKeyVersion.CryptoKeyVersionState.DISABLED:
        current_app.logger.info("Creating new version")
        key_version = key_client.create_crypto_key_version(key_path, {})
        current_app.logger.debug("New key version: {key_version.name}")


    resp = key_client.encrypt(key_version.name, plaintext)

    current_app.logger.info("Successfully encrypted auth info")
    return resp.ciphertext


def upload_encrypted_auth(encrypted_text: bytes, user_id: int, exchange: str) -> Tuple[str, str]:
    current_app.logger.debug(f"Uploading encrypted {exchange} auth for user {user_id}")

    blob_name = f"auth_{exchange}_{user_id}_json"
    auth_bucket = storage_client.get_bucket("catalyst_auth")
    blob = auth_bucket.blob(blob_name)
    blob.upload_from_string(encrypted_text)
    current_app.logger.info(f"Uploaded encrypted auth with blob name {blob_name}")
    return blob_name, auth_bucket.name


def upload_user_auth(exchange_dict: Dict[str, str], user_id: int) -> Tuple[str, str]:
    encrypted = encrypt_user_auth(exchange_dict, user_id)
    return upload_encrypted_auth(encrypted, user_id, exchange_dict["name"])
