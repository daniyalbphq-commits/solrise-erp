# =============================================================================
# S3 bucket for file backups (attachments, private files). The database has its
# own RDS snapshots; this is the off-box half of the backup story.
# =============================================================================

resource "aws_s3_bucket" "backups" {
  count = var.enable_backup_bucket ? 1 : 0

  bucket        = var.backup_bucket_name != "" ? var.backup_bucket_name : "${local.name}-backups-${data.aws_caller_identity.current.account_id}"
  force_destroy = false

  tags = { Name = "${local.name}-backups" }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  count = var.enable_backup_bucket ? 1 : 0

  bucket = aws_s3_bucket.backups[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "backups" {
  count = var.enable_backup_bucket ? 1 : 0

  bucket = aws_s3_bucket.backups[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  count = var.enable_backup_bucket ? 1 : 0

  bucket = aws_s3_bucket.backups[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  count = var.enable_backup_bucket ? 1 : 0

  bucket = aws_s3_bucket.backups[0].id

  rule {
    id     = "expire-old-backups"
    status = "Enabled"

    filter {}

    expiration {
      days = var.backup_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# =============================================================================
# S3 bucket for uploaded files (attachments, images, every File the app writes).
#
# Separate from the backups bucket on purpose: media must never expire, is
# written by the application itself (not by a nightly sync) and needs object
# versioning to stay recoverable - `bench backup --with-files` no longer sees
# these files once they live here.
#
# Deliberately private: the cloud_storage app streams files through Frappe
# (`/api/method/retrieve?key=...` redirects to a short-lived presigned URL) and
# only for callers that pass a File permission check, so the bucket needs no
# public access, no ACLs and no public bucket policy. Public *website* assets are
# covered by the same redirect.
# =============================================================================

resource "aws_s3_bucket" "media" {
  count = var.enable_media_bucket ? 1 : 0

  bucket        = var.media_bucket_name != "" ? var.media_bucket_name : "${local.name}-media-${data.aws_caller_identity.current.account_id}"
  force_destroy = false

  tags = { Name = "${local.name}-media" }
}

resource "aws_s3_bucket_public_access_block" "media" {
  count = var.enable_media_bucket ? 1 : 0

  bucket = aws_s3_bucket.media[0].id

  # All four stay on: nothing reads this bucket anonymously.
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# BucketOwnerEnforced (the default for buckets created after April 2023) turns
# ACLs off entirely. Stated explicitly so the intent is visible and a future
# provider default cannot flip it.
resource "aws_s3_bucket_ownership_controls" "media" {
  count = var.enable_media_bucket ? 1 : 0

  bucket = aws_s3_bucket.media[0].id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Versioning is the media recovery story: a delete in the ERP marks the object
# deleted, a bad overwrite becomes a previous version.
resource "aws_s3_bucket_versioning" "media" {
  count = var.enable_media_bucket ? 1 : 0

  bucket = aws_s3_bucket.media[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  count = var.enable_media_bucket ? 1 : 0

  bucket = aws_s3_bucket.media[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  count = var.enable_media_bucket ? 1 : 0

  bucket = aws_s3_bucket.media[0].id

  # No `expiration`: uploaded files are the system of record and are kept.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "expire-superseded-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.media_noncurrent_version_retention_days
    }
  }
}

# Refuse plaintext access to the bucket (the app always talks HTTPS; this keeps
# a misconfigured client from leaking signed credentials or file bytes).
resource "aws_s3_bucket_policy" "media" {
  count = var.enable_media_bucket ? 1 : 0

  bucket = aws_s3_bucket.media[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.media[0].arn,
          "${aws_s3_bucket.media[0].arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}

# =============================================================================
# ECR (optional): build the image once on a workstation/CI and let EC2 pull it,
# instead of compiling erpnext + hrms on the production host.
# =============================================================================

resource "aws_ecr_repository" "app" {
  count = var.enable_ecr ? 1 : 0

  name                 = var.ecr_repository_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "${local.name}-ecr" }
}

resource "aws_ecr_lifecycle_policy" "app" {
  count = var.enable_ecr ? 1 : 0

  repository = aws_ecr_repository.app[0].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the last 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = { type = "expire" }
      },
    ]
  })
}
