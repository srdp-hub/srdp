output "instance_name" {
  description = "The name of the Compute Engine instance."
  value       = google_compute_instance.vm_instance.name
}

output "static_ip_address" {
  description = "The static public IP address of the VM."
  value       = google_compute_address.static_ip.address
}
