FROM python:3.12-slim

WORKDIR /app

RUN python -m pip install --no-cache-dir "httpx==0.28.1"

COPY tests/__init__.py tests/__init__.py
COPY tests/integration/__init__.py tests/integration/__init__.py
COPY tests/integration/fake_github.py tests/integration/fake_github.py

CMD ["python", "-m", "tests.integration.fake_github"]
