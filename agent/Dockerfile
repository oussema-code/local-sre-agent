FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY sre_agent ./sre_agent
RUN pip install --no-cache-dir .
EXPOSE 9200
CMD ["python", "-m", "sre_agent"]
