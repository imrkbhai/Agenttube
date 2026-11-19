"""Clip Studio: turn long videos into review-ready Shorts and long-form clips."""
import truststore

# Use the Windows/macOS certificate store so downloads work behind TLS-inspecting networks.
truststore.inject_into_ssl()
