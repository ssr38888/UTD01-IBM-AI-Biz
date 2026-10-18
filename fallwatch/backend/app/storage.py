"""Where image bytes live: a folder on the LinuxONE box (default) or S3-compatible object storage."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("bad key")
        return p

    def put(self, key: str, data: bytes, content_type: str) -> None:
        p = self._path(key)
        if p.exists():
            raise FileExistsError(key)  # evidence is write-once
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()


class S3Storage:
    def __init__(self, client=None):
        import boto3
        from botocore.config import Config

        self.bucket = config.S3_BUCKET
        self.retention_days = config.S3_RETENTION_DAYS
        self.s3 = client or boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            region_name=config.S3_REGION,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        names = {b["Name"] for b in self.s3.list_buckets().get("Buckets", [])}
        if self.bucket not in names:
            kwargs = {"Bucket": self.bucket}
            if self.retention_days > 0:
                kwargs["ObjectLockEnabledForBucket"] = True  # must be set at creation
            self.s3.create_bucket(**kwargs)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        kwargs = {"Bucket": self.bucket, "Key": key, "Body": data, "ContentType": content_type}
        if self.retention_days > 0:
            kwargs["ObjectLockMode"] = "COMPLIANCE"
            kwargs["ObjectLockRetainUntilDate"] = datetime.now(timezone.utc) + timedelta(days=self.retention_days)
        self.s3.put_object(**kwargs)

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()


def make_storage():
    if config.STORAGE_BACKEND == "s3":
        return S3Storage()
    return LocalStorage(config.LOCAL_STORAGE_DIR)
