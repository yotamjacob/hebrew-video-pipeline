"""One-off R2 bucket setup for direct browser uploads:
- CORS: allow PUT from the app origin, expose ETag (the browser must read it
  to complete a multipart upload)
- Lifecycle: expire orphaned uploads/ objects and abort stale multipart
  uploads after 2 days (the happy path deletes the object at complete-time)
Run: modal run scripts/r2_bucket_setup.py
Needs an R2 API token with Admin Read & Write on the bucket (the object-level
backup token gets AccessDenied on PutBucketCors).
"""
import modal

app = modal.App("r2-setup")
image = modal.Image.debian_slim().pip_install("boto3")

ORIGINS = [
    "https://hebrew-pipeline.app",
    "https://www.hebrew-pipeline.app",
]


@app.function(image=image, secrets=[modal.Secret.from_name("hebpipe-backup")])
def setup():
    import os
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("S3_REGION", "auto"),
    )
    bucket = os.environ["S3_BUCKET"]

    s3.put_bucket_cors(
        Bucket=bucket,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedOrigins": ORIGINS,
                    # PUT = upload parts; DELETE = the uplink probe cleaning up
                    # its scratch objects via presigned DELETEs.
                    "AllowedMethods": ["PUT", "DELETE"],
                    "AllowedHeaders": ["*"],
                    # ETag exposure is LOAD-BEARING: without it every part PUT
                    # "succeeds" but JS can't read the ETag and the multipart
                    # can never be completed.
                    "ExposeHeaders": ["ETag"],
                    "MaxAgeSeconds": 86400,
                }
            ]
        },
    )
    print("CORS set:", s3.get_bucket_cors(Bucket=bucket)["CORSRules"])

    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "expire-stale-uploads",
                    "Filter": {"Prefix": "uploads/"},
                    "Status": "Enabled",
                    "Expiration": {"Days": 2},
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 2},
                }
            ]
        },
    )
    print("Lifecycle set:",
          s3.get_bucket_lifecycle_configuration(Bucket=bucket)["Rules"])

    # Clean up today's probe objects while we're here.
    for i in list(range(6)) + [""]:
        key = f"uploads/_probe_delete_me{f'_{i}' if i != '' else ''}.bin"
        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass
    print("probe objects cleaned")


@app.local_entrypoint()
def main():
    setup.remote()
