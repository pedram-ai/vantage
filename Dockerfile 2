FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/srv PORT=8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
