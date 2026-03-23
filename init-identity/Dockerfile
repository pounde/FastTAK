FROM python:3.12-alpine

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY bootstrap.py /app/bootstrap.py

ENTRYPOINT ["python", "-u", "/app/bootstrap.py"]
