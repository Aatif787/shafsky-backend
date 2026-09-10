# Global airport reference data (OurAirports).
#
# Preferred: commit `airports.csv` so offline/local builds work without network.
# Fallback: Docker and CI download it automatically if the file is missing.
#
# Refresh manually:
#   python tools/download_airports_csv.py
