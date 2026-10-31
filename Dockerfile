FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements-free.txt || true
EXPOSE 8787
VOLUME /app/data
CMD ["python", "app.py", "--no-browser", "--port", "8787"]
