resource "aws_glue_job" "customer_events_ingestion" {
  name     = "customer-events-ingestion"
  role_arn = var.glue_role_arn
  command {
    script_location = "s3://analytics-artifacts/pipelines/glue/customer_events_job.py"
  }
}

resource "aws_s3_bucket" "analytics_lake" {
  bucket = "analytics-lake"
}
