# SaccoSphere

## PDF Statement Generation On Render

SaccoSphere uses WeasyPrint to generate member statement PDFs. WeasyPrint needs
native system libraries in addition to the Python package.

On Render, install the required system packages during build before running
`pip install -r requirements.txt`. A typical Debian-based setup is:

```bash
apt-get update && apt-get install -y \
  libpango-1.0-0 \
  libpangoft2-1.0-0 \
  libcairo2 \
  libgdk-pixbuf-2.0-0 \
  libffi-dev \
  shared-mime-info
```

If these packages are missing, the JSON statement endpoint will still work, but
PDF generation may return `503 PDF generation temporarily unavailable.`
