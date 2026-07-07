FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Broker API by default; the compose file overrides this for the
# dashboard service. Kept as one image for both, since they share the
# exact same aui/ package and there's no reason to build it twice.
EXPOSE 8000
CMD ["uvicorn", "aui.broker.app:app", "--host", "0.0.0.0", "--port", "8000"]
