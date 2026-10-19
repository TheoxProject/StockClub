"""Photo storage with local development storage and optional S3/R2 support."""
from __future__ import annotations

import io
import os
import uuid
from pathlib import Path

import boto3
from botocore.config import Config
from PIL import Image, ImageOps, UnidentifiedImageError
from flask import current_app, url_for
from werkzeug.datastructures import FileStorage


class InvalidImage(ValueError):
    pass


def _s3_enabled() -> bool:
    return all(
        current_app.config.get(key)
        for key in ("S3_BUCKET", "S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
    )


def _s3_client():
    region = current_app.config.get("S3_REGION")
    if not region or region == "auto":
        region = "eu-central-1"
    return boto3.client(
        "s3",
        endpoint_url=current_app.config["S3_ENDPOINT_URL"],
        aws_access_key_id=current_app.config["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=current_app.config["S3_SECRET_ACCESS_KEY"],
        region_name=region,
        config=Config(s3={"addressing_style": "path"}),
    )


def _normalise_image(file: FileStorage) -> bytes:
    """Verify the file and re-encode it as WebP to strip EXIF/GPS metadata."""
    if not file or not file.filename:
        raise InvalidImage("Choisis une photo pour cet objet.")

    file.stream.seek(0)
    try:
        with Image.open(file.stream) as probe:
            if probe.format not in {"JPEG", "PNG", "WEBP"}:
                raise InvalidImage("Format non accepté. Utilise une photo JPG, PNG ou WebP.")
            probe.verify()

        file.stream.seek(0)
        with Image.open(file.stream) as image:
            # A phone JPEG may have 50+ megapixels. JPEG draft decoding reduces
            # the working image *before* applying EXIF rotation and resizing.
            if image.format == "JPEG":
                image.draft("RGB", (1600, 1600))
            elif image.width * image.height > 25_000_000:
                raise InvalidImage("Image trop grande. Choisis une photo de 25 Mpx maximum ou utilise le mode photo standard.")
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1600, 1600))
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=82, method=4)
            return output.getvalue()
    except InvalidImage:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidImage("Le fichier sélectionné ne semble pas être une image valide.") from exc


def save_photo(file: FileStorage) -> str:
    payload = _normalise_image(file)
    key = f"items/{uuid.uuid4().hex}.webp"

    if _s3_enabled():
        _s3_client().put_object(
            Bucket=current_app.config["S3_BUCKET"],
            Key=key,
            Body=payload,
            ContentType="image/webp",
            CacheControl="public, max-age=86400",
        )
    else:
        root = Path(current_app.config["UPLOAD_FOLDER"])
        destination = root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    return key


def delete_photo(key: str | None) -> None:
    if not key:
        return
    try:
        if _s3_enabled():
            _s3_client().delete_object(Bucket=current_app.config["S3_BUCKET"], Key=key)
        else:
            path = Path(current_app.config["UPLOAD_FOLDER"]) / key
            path.resolve().relative_to(Path(current_app.config["UPLOAD_FOLDER"]).resolve())
            path.unlink(missing_ok=True)
    except Exception:
        current_app.logger.exception("Impossible de supprimer la photo %s", key)


def photo_url(key: str | None) -> str | None:
    if not key:
        return None
    if _s3_enabled():
        public_base = current_app.config.get("S3_PUBLIC_BASE_URL", "").rstrip("/")
        if public_base:
            return f"{public_base}/{key}"
        return _s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": current_app.config["S3_BUCKET"], "Key": key},
            ExpiresIn=3600,
        )
    return url_for("inventory.uploaded_photo", filename=key)
