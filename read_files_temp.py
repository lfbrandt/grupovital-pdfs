
# temp helper - delete after
import os
files = [
    r"app/routes/compress.py",
    r"app/services/compress_service.py",
    r"app/services/sanitize_service.py",
    r"app/utils/pdf_utils.py",
]
for f in files:
    print(f"=== {f} ===")
    try:
        with open(f) as fh:
            print(fh.read())
    except Exception as e:
        print(f"ERROR: {e}")
