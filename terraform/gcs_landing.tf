# Landing zone for source adapters (stage worker scans landing/{source}/)
resource "google_storage_bucket_object" "landing_prefix" {
  name    = "landing/"
  bucket  = google_storage_bucket.data_lake.name
  content = " "
}

resource "google_storage_bucket_object" "landing_mobile" {
  name    = "landing/mobile_app/"
  bucket  = google_storage_bucket.data_lake.name
  content = " "
}

resource "google_storage_bucket_object" "landing_app_log" {
  name    = "landing/app_log/"
  bucket  = google_storage_bucket.data_lake.name
  content = " "
}
