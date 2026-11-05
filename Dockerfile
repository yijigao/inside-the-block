FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY inside_block ./inside_block
COPY config.json ./config.json
# keys/ and data/ are mounted at runtime (see docker-compose.yml); never bake keys into the image.
ENTRYPOINT ["python", "-m", "inside_block.probe"]
CMD ["--help"]
