# lib/file_storage_module.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple, Union

import boto3
import paramiko
from pydub import AudioSegment

# Local audio directory (same as your old _write_mp3)
_AUDIO_DIR = Path("static/audio")

# SSH key root – must match where you store per-system keys
SSH_KEY_ROOT = Path("var/.ssh")


def _write_mp3(seg: AudioSegment, audio_archive_path: Union[str, Path], file_name: str) -> Tuple[str, str]:
    """
    Encode *seg* as MP3, store under static/audio/YYYY/MM/ and
    return the relative path (e.g. "static/audio/2025/07/rs2_abcd.mp3").
    """
    now = datetime.now(timezone.utc)

    audio_archive_path = Path(audio_archive_path)  # ✅ normalize str -> Path

    subdir = audio_archive_path / f"{now:%Y/%m}"
    subdir.mkdir(parents=True, exist_ok=True)

    fullpath = subdir / file_name
    seg.export(str(fullpath), format="mp3", bitrate="64k")

    static_root = audio_archive_path.parent
    web_rel = fullpath.relative_to(static_root).as_posix()
    web_rel = f"static/{web_rel}"

    return str(fullpath), web_rel


def _build_audio_url(local_audio_path: str) -> str:
    """
    Build a client-facing URL for a locally-written audio file.

    - If AUDIO_BASE_URL / PUBLIC_BASE_URL is set:
        * Ensure it has http/https scheme (default https)
        * Return '<base>/<static/audio/...>'
    - Otherwise:
        * Return a root-relative URL: '/static/audio/...'
    """
    # Always make it look like a web path
    rel = "/" + local_audio_path.lstrip("/")  # '/static/audio/...'

    base = os.getenv("BASE_URL")
    if base:
        base = base.strip()

        # If the base has no scheme, default to https://
        if not base.startswith("http://") and not base.startswith("https://"):
            # handle both 'codeholio.icaddispatch.com' and '//codeholio...'
            base = "https://" + base.lstrip("/")

        return base.rstrip("/") + rel

    # No base configured → root-relative URL
    return rel


def _store_audio_for_system(
        db,  # kept for signature compatibility; not used here
        radio_system_id: int,
        seg: AudioSegment,
        file_name: str,
        storage_cfg: dict,
        audio_archive_path,
        logger,
) -> Tuple[str, str]:
    """
    Main storage dispatcher.

    Returns:
      (value_for_db, audio_url_for_clients)

    IMPORTANT:
      • value_for_db is ALWAYS a URL (absolute if possible, otherwise root-relative)
      • audio_url_for_clients is the same URL (kept separate for future flexibility)

    Always writes a local MP3 first so LOCAL behavior works, and remote
    backends can upload from disk. For SFTP/S3, the local file is deleted
    after a successful upload.
    """
    # Always write the local file like before
    local_audio_path, web_rel = _write_mp3(seg, audio_archive_path, file_name)
    local_audio_url = _build_audio_url(web_rel)

    storage_type = (storage_cfg.get("storage_type") or "LOCAL").upper()

    # -------------------------------------------------
    # LOCAL – keep file, DB stores local URL
    # -------------------------------------------------
    if storage_type == "LOCAL":
        return local_audio_url, local_audio_url

    # -------------------------------------------------
    # SFTP – upload, then delete local file
    # -------------------------------------------------
    if storage_type == "SFTP":
        try:
            remote_rel_path, remote_url = _upload_to_sftp(
                radio_system_id=radio_system_id,
                local_audio_path=local_audio_path,
                file_name=file_name,
                storage_cfg=storage_cfg,
                logger=logger,
            )

            # Prefer remote URL; fall back to local URL if base_url isn't set
            final_url = remote_url

            # Delete local file after successful upload
            try:
                os.remove(local_audio_path)
            except OSError as e:
                logger.warning(
                    "Failed to delete local audio file %s after SFTP upload: %s",
                    local_audio_path, e
                )

            # DB stores URL, frontend uses URL
            return final_url, final_url

        except Exception as e:
            logger.exception(
                "SFTP upload failed for radio_system_id=%s: %s",
                radio_system_id, e
            )
            # Hard fail so DB never points at a non-existent remote file
            raise

    # -------------------------------------------------
    # S3 / S3-compatible (MinIO / Wasabi) – upload, then delete local file
    # -------------------------------------------------
    if storage_type == "S3":
        try:
            s3_key, s3_url = _upload_to_s3(
                local_audio_path=local_audio_path,
                file_name=file_name,
                storage_cfg=storage_cfg,
                logger=logger,
            )

            final_url = s3_url

            # Delete local file after successful upload
            try:
                os.remove(local_audio_path)
            except OSError as e:
                logger.warning(
                    "Failed to delete local audio file %s after S3 upload: %s",
                    local_audio_path, e
                )

            return final_url, final_url

        except Exception as e:
            logger.exception(
                "S3 upload failed for radio_system_id=%s: %s",
                radio_system_id, e
            )
            raise

    # -------------------------------------------------
    # Unknown storage type → fallback to LOCAL semantics
    # -------------------------------------------------
    logger.warning(
        "Unknown storage_type=%r for radio_system_id=%s → LOCAL fallback",
        storage_type, radio_system_id
    )
    return local_audio_url, local_audio_url


def _upload_to_sftp(
        radio_system_id: int,
        local_audio_path: str,
        file_name: str,
        storage_cfg: dict,
        logger,
) -> Tuple[str, str]:
    """
    Uploads the already-written local file to SFTP.

    Returns:
      (remote_relative_path, public_url_or_empty)
    """
    sftp_cfg = storage_cfg.get("sftp") or {}
    host = sftp_cfg.get("host")
    username = sftp_cfg.get("username")
    port = int(sftp_cfg.get("port") or 22)
    timeout_s = int(sftp_cfg.get("timeout_s") or 30)
    password = sftp_cfg.get("password") or None
    use_ssh_key = bool(sftp_cfg.get("use_ssh_key"))
    remote_root = (sftp_cfg.get("remote_path") or "").rstrip("/")  # e.g. /var/calls
    base_url = (sftp_cfg.get("base_url") or "").rstrip("/")         # e.g. https://files.example.com

    if not host or not username:
        raise RuntimeError("SFTP storage misconfigured: host and username are required")

    path_pattern = storage_cfg.get("path_pattern") or "%Y/%m/%d"
    now = datetime.now(timezone.utc)
    subdir = now.strftime(path_pattern).strip("/")  # e.g. 2025/12/10

    # Relative path and full remote path
    rel_parts = [p for p in (subdir, file_name) if p]
    remote_rel_path = "/".join(rel_parts)  # e.g. 2025/12/10/file.mp3

    if remote_root:
        remote_full_path = f"{remote_root}/{remote_rel_path}"
    else:
        remote_full_path = remote_rel_path

    logger.debug("SFTP upload: host=%s remote_full_path=%s", host, remote_full_path)

    # --- paramiko connection setup ---
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    pkey = None
    if use_ssh_key:
        key_path = SSH_KEY_ROOT / str(radio_system_id) / "id_rsa"
        if not key_path.exists():
            raise RuntimeError(f"SFTP SSH key not found at {key_path}")
        pkey = paramiko.RSAKey.from_private_key_file(str(key_path))

    connect_kwargs = {
        "hostname": host,
        "port": port,
        "username": username,
        "timeout": timeout_s,
        "banner_timeout": timeout_s,
        "auth_timeout": timeout_s,
    }

    if pkey is not None:
        connect_kwargs["pkey"] = pkey
    else:
        connect_kwargs["password"] = password

    client.connect(**connect_kwargs)

    try:
        sftp = client.open_sftp()
        try:
            _sftp_mkdirs(sftp, os.path.dirname(remote_full_path))
            sftp.put(local_audio_path, remote_full_path)
        finally:
            sftp.close()
    finally:
        client.close()

    # Build public URL if base_url present
    public_url = ""
    if base_url:
        public_url = f"{base_url}/{remote_rel_path}"

    return remote_rel_path, public_url


def _sftp_mkdirs(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    """
    Recursively mkdir -p on SFTP. remote_dir like '/var/calls/2025/12/10'.
    """
    if not remote_dir:
        return

    parts = remote_dir.strip("/").split("/")
    path = ""
    for p in parts:
        path = f"{path}/{p}" if path else f"/{p}"
        try:
            sftp.stat(path)
        except IOError:
            sftp.mkdir(path)


def _upload_to_s3(
        local_audio_path: str,
        file_name: str,
        storage_cfg: dict,
        logger,
) -> Tuple[str, str]:
    """
    Uploads the local file to S3-compatible storage (AWS S3, MinIO, Wasabi, etc).

    Returns:
      (object_key, public_url)

    For MinIO / Wasabi:
      - Set s3_endpoint_url to your object endpoint
        e.g. "https://minio.example.com" or "https://s3.us-east-2.wasabisys.com"
      - Bucket + object_key_prefix + path_pattern behave the same as AWS.
    """
    s3_cfg = storage_cfg.get("s3") or {}
    bucket = s3_cfg.get("bucket")
    region = s3_cfg.get("region")
    access_key_id = s3_cfg.get("access_key_id")
    secret_access_key = s3_cfg.get("secret_access_key")
    endpoint_url = s3_cfg.get("endpoint_url") or None  # MinIO/Wasabi endpoint; None = AWS
    object_key_prefix = (s3_cfg.get("object_key_prefix") or "").strip("/")

    if not bucket:
        raise RuntimeError("S3 storage misconfigured: bucket is required")

    path_pattern = storage_cfg.get("path_pattern") or "%Y/%m/%d"
    now = datetime.now(timezone.utc)
    subdir = now.strftime(path_pattern).strip("/")

    key_parts = []
    if object_key_prefix:
        key_parts.append(object_key_prefix)
    if subdir:
        key_parts.append(subdir)
    key_parts.append(file_name)
    object_key = "/".join(key_parts)  # e.g. calls/2025/12/10/file.mp3

    logger.debug(
        "S3 upload: bucket=%s key=%s endpoint=%s", bucket, object_key, endpoint_url
    )

    client_kwargs = {}
    if access_key_id and secret_access_key:
        client_kwargs["aws_access_key_id"] = access_key_id
        client_kwargs["aws_secret_access_key"] = secret_access_key
    if region:
        client_kwargs["region_name"] = region

    # This works for AWS S3, MinIO, and Wasabi; endpoint_url switches to path-style
    s3 = boto3.client("s3", endpoint_url=endpoint_url, **client_kwargs)

    s3.upload_file(local_audio_path, bucket, object_key)

    # Public URL construction:
    #  - AWS S3 without endpoint_url → standard virtual-host style
    #  - endpoint_url set → path-style: <endpoint>/<bucket>/<key>
    if endpoint_url:
        base = endpoint_url.rstrip("/")
        public_url = f"{base}/{bucket}/{object_key}"
    elif region:
        public_url = f"https://{bucket}.s3.{region}.amazonaws.com/{object_key}"
    else:
        public_url = f"https://{bucket}.s3.amazonaws.com/{object_key}"

    return object_key, public_url
