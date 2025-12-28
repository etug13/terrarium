FROM python:3.13-slim

WORKDIR /bridge

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /bridge

CMD ["python", "bridge.py"]