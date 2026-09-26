FROM python:3.12-slim
WORKDIR /app
COPY handoff_kit ./handoff_kit
COPY fixtures ./fixtures
COPY web ./web
COPY demo.py ./demo.py
RUN useradd --uid 10001 --create-home demo
USER demo
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080
CMD ["python", "-m", "handoff_kit.web", "--host", "0.0.0.0"]
